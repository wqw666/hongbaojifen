import type { NapCatPluginContext } from './napcat-shim.js';
import fs from 'fs';
import path from 'path';
import {
  clearAllRecords,
  config,
  getRecord,
  queryByGroupAndTime,
  records,
  saveConfig,
  upsertRecord,
} from './store.js';
import {
  combineClaimsFromDetail,
  diagnoseRedPacketRaw,
  extractGrayTipClaim,
  extractRecvListFromRaw,
  extractWallet,
  extractWalletFromRaw,
  formatTime,
  grabRedBag,
  getMsgService,
  isValidPullDetailRaw,
  mergeClaims,
  normalizeClaims,
  normalizeWalletFields,
  parseSendOrderSummary,
  pullDetail,
  exhaustPullDetailAttempts,
  scorePullDetailRaw,
  randomDelay,
  repairBadClaims,
  inferMissingAmounts,
  scanGrayTipsFromHistory,
  scanWalletMetaFromHistory,
  sendPasswordIfNeeded,
  sleep,
  isValidUin,
} from './redpacket.js';

/** 与 redpacket 内 sanitizeAuthKey 相同：只留 32 hex */
function sanitizeAuthKey(v: unknown): string {
  const s = String(v || '').trim();
  const m = s.match(/[0-9a-fA-F]{32}/);
  return m ? m[0].toLowerCase() : '';
}
import type { ClaimRecord, RedPacketRecord, RedPacketSummary, WalletContext } from './types.js';

/** 防止同一红包被 OneBot + 内核监听重复处理 */
const processingBills = new Set<string>();

function ok(res: any, data: unknown) {
  res.json({ code: 0, message: 'ok', data });
}

function fail(res: any, message: string, code = -1, status = 400) {
  res.status(status).json({ code, message });
}

function unwrapActionData(raw: unknown): unknown {
  if (raw == null) return null;
  if (Array.isArray(raw)) return raw;
  if (typeof raw !== 'object') return raw;
  const o = raw as Record<string, unknown>;
  if ('data' in o) return o.data;
  return raw;
}

function unwrapActionList(raw: unknown): any[] {
  const data = unwrapActionData(raw);
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    const o = data as Record<string, unknown>;
    for (const k of ['members', 'memberList', 'list']) {
      if (Array.isArray(o[k])) return o[k] as any[];
    }
  }
  return [];
}

function roleLabel(role: unknown): string {
  const r = String(role || 'member').toLowerCase();
  if (r === 'owner' || r === '2') return '群主';
  if (r === 'admin' || r === '1') return '管理';
  return '成员';
}

function normalizeMember(m: any) {
  const uin = String(m?.user_id ?? m?.uin ?? m?.qq ?? '');
  const nick = String(m?.nickname ?? m?.nick ?? '');
  const card = String(m?.card ?? m?.cardName ?? '');
  const role = String(m?.role ?? 'member').toLowerCase();
  return {
    uin,
    nickname: nick,
    card,
    display_name: card || nick || uin,
    role,
    role_text: roleLabel(role),
  };
}

function parseTimeBound(v: unknown, fallback: number): number {
  if (v === undefined || v === null || v === '') return fallback;
  const s = String(v).trim();
  if (/^\d+$/.test(s)) {
    const n = Number(s);
    return n > 1e12 ? Math.floor(n / 1000) : n;
  }
  const t = Date.parse(s.replace(/-/g, '/'));
  if (!Number.isNaN(t)) return Math.floor(t / 1000);
  return fallback;
}

function walletFromRecord(r: RedPacketRecord): WalletContext {
  const w = normalizeWalletFields({
    walletElement: null,
    billNo: r.billNo,
    peerUid: r.peerUid,
    peerUin: r.groupId,
    senderUin: r.senderUin,
    senderName: r.senderName,
    peerName: r.groupName,
    chatType: r.chatType,
    msgSeq: r.msgSeq,
    msgTime: r.msgTime,
    wishing: r.wishing,
    pcBody: r.pcBody,
    stringIndex: r.stringIndex,
    redChannel: r.redChannel || 0,
  });
  return w as WalletContext;
}

function needsDetailRefresh(rec: RedPacketRecord): boolean {
  if (!rec.pcBody) return false;
  const summary = rec.summary || parseSendOrderSummary(rec.rawDetail);
  const claimCount = rec.claims?.length || 0;
  const withAmount = (rec.claims || []).filter((c) => (c.amount || 0) > 0).length;
  if (!summary) return claimCount === 0;
  // 名单人数/金额不全：必须再拉（原生字段已能拿到历史 recvdOrderList）
  if (summary.recvNum > claimCount) return true;
  if (summary.recvNum > 0 && withAmount < summary.recvNum) return true;
  if (summary.recvNum < summary.totalNum && Date.now() - rec.updatedAt > 60_000) return true;
  // 名单已齐：短时间内不再反复 pullDetail
  if (summary.recvNum > 0 && claimCount >= summary.recvNum && withAmount >= summary.recvNum) {
    const age = Date.now() - (rec.updatedAt || 0);
    if (age < 10 * 60 * 1000) return false;
  }
  return false;
}

/** pullDetail 的 recvName 常串号/乱码；用群成员 card/昵称覆盖 */
async function enrichClaimsFromGroupMembers(
  ctx: NapCatPluginContext,
  groupId: string,
  claims: ClaimRecord[]
): Promise<ClaimRecord[]> {
  if (!groupId || !claims.length) return claims;
  const need = claims.some((c) => {
    if (!isValidUin(c.uin)) return false;
    const n = String(c.name || '').trim();
    if (!n || n === c.uin || n.startsWith('未知领取人')) return true;
    // 多人同名且等于其中一个可疑串名时也重写
    return false;
  });
  const names = claims.map((c) => String(c.name || '').trim()).filter(Boolean);
  const allSame =
    names.length >= 2 && names.every((n) => n === names[0]) && names[0] !== claims[0]?.uin;
  if (!need && !allSame) return claims;

  try {
    const listRaw = await ctx.actions.call(
      'get_group_member_list',
      { group_id: groupId, no_cache: false },
      ctx.adapterName,
      ctx.pluginManager.config
    );
    const map = new Map<string, string>();
    for (const m of unwrapActionList(listRaw).map(normalizeMember)) {
      if (m.uin && m.display_name) map.set(m.uin, m.display_name);
    }
    if (!map.size) return claims;
    return claims.map((c) => {
      const dn = map.get(String(c.uin));
      if (!dn) return c;
      return { ...c, name: dn };
    });
  } catch {
    return claims;
  }
}

async function refreshRecordDetail(ctx: NapCatPluginContext, rec: RedPacketRecord) {
  const prevSummary = rec.summary || parseSendOrderSummary(rec.rawDetail);
  let detail: { claims: ClaimRecord[]; summary?: RedPacketSummary; raw: unknown } | null = null;
  try {
    detail = await pullDetail(ctx, walletFromRecord(rec));
  } catch (e) {
    ctx.logger?.warn?.('[红包监控] pullDetail 失败，保留旧缓存', e);
  }

  let claims = rec.claims || [];
  try {
    const fromHistory = await scanGrayTipsFromHistory(ctx, rec);
    if (fromHistory.length) {
      claims = mergeClaims(claims, fromHistory);
      ctx.logger?.info?.(
        `[红包监控] 历史灰条补录 billNo=${rec.billNo.slice(-8)} +${fromHistory.length} 人`
      );
    }
  } catch (e) {
    ctx.logger?.warn?.('[红包监控] 历史灰条扫描失败', e);
  }

  if (detail && isValidPullDetailRaw(detail.raw)) {
    const combined = combineClaimsFromDetail(claims, detail.raw, undefined, {
      senderUin: rec.senderUin,
      senderName: rec.senderName,
    });
    const enriched = await enrichClaimsFromGroupMembers(ctx, rec.groupId, combined.claims);
    return upsertRecord({
      ...rec,
      claims: enriched,
      summary: combined.summary || prevSummary,
      rawDetail: detail.raw,
      updatedAt: Date.now(),
    });
  }

  const repaired = repairBadClaims({ claims, summary: prevSummary, rawDetail: rec.rawDetail });
  const enriched = await enrichClaimsFromGroupMembers(ctx, rec.groupId, repaired);
  return upsertRecord({
    ...rec,
    claims: enriched,
    summary: prevSummary,
    updatedAt: Date.now(),
  });
}

/** onGrabPasswordRedBag 回调：补全领取人金额 */
export async function onGrabRedBagNotify(
  ctx: NapCatPluginContext,
  info: { billNo: string; uin: string; name: string; amount: number; time: number }
) {
  const rec = getRecord(info.billNo);
  if (!rec) return;
  const claims = mergeClaims(rec.claims || [], [
    {
      uin: info.uin,
      name: info.name || info.uin,
      amount: info.amount,
      time: info.time,
      timeText: formatTime(info.time),
    },
  ]);
  upsertRecord({ ...rec, claims, updatedAt: Date.now() });
  ctx.logger?.info?.(
    `[红包监控] 内核领取通知 ${info.name || info.uin} ${info.amount} bill=${info.billNo.slice(-8)}`
  );
}

export function registerRoutes(ctx: NapCatPluginContext) {
  const router = ctx.router;

  /** 调试：扫描群历史灰条 */
  router.getNoAuth('/debug/scan', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      const rec = billNo ? getRecord(billNo) : null;
      if (!rec) return fail(res, '缺少 bill_no 或本地无记录');
      const msgApi = ctx.core?.apis?.MsgApi;
      const peer = { chatType: 2, peerUid: String(rec.groupId), guildId: '' };
      const stats: Record<string, unknown> = { billNo, msgSeq: rec.msgSeq, msgTime: rec.msgTime };
      if (msgApi?.getMsgsBySeqAndCount) {
        for (const label of ['seq_false', 'seq_true'] as const) {
          const order = label === 'seq_true';
          try {
            const rsp = await msgApi.getMsgsBySeqAndCount(
              peer,
              String(rec.msgSeq),
              80,
              order,
              true
            );
            const msgList = rsp?.msgList || [];
            let gray = 0;
            let matched = 0;
            const samples: unknown[] = [];
            for (const raw of msgList) {
              const els = raw?.elements || [];
              for (const el of els) {
                const g =
                  el?.grayTipElement?.jsonGrayTipElement ||
                  el?.jsonGrayTipElement ||
                  el?.grayTipElement;
                if (!g) continue;
                gray += 1;
                const w = extractWalletFromRaw(raw);
                if (w?.billNo === billNo) {
                  matched += 1;
                  if (samples.length < 5) {
                    samples.push({
                      msgSeq: raw.msgSeq,
                      msgTime: raw.msgTime,
                      busiId: g.busiId,
                      jsonStr: String(g.jsonStr || '').slice(0, 300),
                      claim: extractGrayTipClaim(w!),
                    });
                  }
                }
              }
            }
            stats[label] = { total: msgList.length, gray, matched, samples };
          } catch (e) {
            stats[label] = { error: String(e) };
          }
        }
      }
      if (msgApi?.getAioFirstViewLatestMsgs) {
        try {
          const rsp = await msgApi.getAioFirstViewLatestMsgs(peer, 200);
          const msgList = rsp?.msgList || [];
          let gray = 0;
          let matched = 0;
          const samples: unknown[] = [];
          const busiIds: Record<string, number> = {};
          const all19357: unknown[] = [];
          for (const raw of msgList) {
            for (const el of raw?.elements || []) {
              const g =
                el?.grayTipElement?.jsonGrayTipElement ||
                el?.jsonGrayTipElement ||
                el?.grayTipElement;
              if (!g) continue;
              gray += 1;
              const bid = String(g.busiId || '?');
              busiIds[bid] = (busiIds[bid] || 0) + 1;
              const w = extractWalletFromRaw(raw);
              if (bid === '19357' && all19357.length < 20) {
                all19357.push({
                  seq: raw.msgSeq,
                  time: raw.msgTime,
                  listid: w?.billNo ? w.billNo.slice(-12) : null,
                  claim: w ? extractGrayTipClaim(w) : null,
                });
              }
              const hit = w?.billNo === billNo;
              if (hit) matched += 1;
              if (hit && samples.length < 8) {
                samples.push({
                  msgSeq: raw.msgSeq,
                  msgTime: raw.msgTime,
                  busiId: g.busiId,
                  claim: w ? extractGrayTipClaim(w) : null,
                });
              }
            }
          }
          stats.latest = { total: msgList.length, gray, matched, busiIds, all19357, samples };
        } catch (e) {
          stats.latest = { error: String(e) };
        }
      }
      const claims = await scanGrayTipsFromHistory(ctx, rec);
      ok(res, { stats, claims });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /**
   * 调试：tenpay.com pskey + HTTP hb_pc_detail / SSO token / channel+busType pullDetail
   * （PC RedBagWorker 走 GetPsKey(tenpay.com) → SsoGetToken → hb_pc_detail）
   */
  router.getNoAuth('/debug/tenpay_detail', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec?.pcBody) return fail(res, '缺少 bill_no 或 pcBody');

      const selfUin = String(ctx.core?.selfInfo?.uin || '');
      const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
      const authKey = sanitizeAuthKey(rec.authKey || '');
      const stringIndex = String(rec.stringIndex || '');
      const groupId = String(rec.groupId || '');
      const fullBill = rec.billNo;
      const pcBody = String(rec.pcBody || '').replace(/^0x/i, '');
      const channel = Number(rec.redChannel || 1) || 1;
      const busType = 2;

      const out: Record<string, unknown> = {
        billNo: fullBill,
        authKey: authKey.slice(0, 8) + '…',
        stringIndex: stringIndex.slice(0, 8) + '…',
      };

      // 1) TipOff getPskey(tenpay.com)
      let pskey = '';
      try {
        const tip = ctx.core?.context?.session?.getTipOffService?.();
        const map = await tip?.getPskey?.(['tenpay.com'], true);
        const domainMap = map?.domainPskeyMap;
        if (domainMap?.get) pskey = String(domainMap.get('tenpay.com') || '');
        else if (domainMap && typeof domainMap === 'object') {
          pskey = String((domainMap as any)['tenpay.com'] || '');
        }
        out.pskeyLen = pskey.length;
        out.pskeyPrefix = pskey ? pskey.slice(0, 6) + '…' : '';
      } catch (e) {
        out.pskeyError = String(e);
      }

      // 2) UserApi cookies / getPSkey
      let cookies: Record<string, string> = {};
      try {
        const userApi = ctx.core?.apis?.UserApi;
        if (userApi?.getCookies) {
          cookies = (await userApi.getCookies('tenpay.com')) || {};
          out.cookieKeys = Object.keys(cookies);
          if (!pskey && cookies.p_skey) pskey = String(cookies.p_skey);
        }
        if (!pskey && userApi?.getPSkey) {
          const m = await userApi.getPSkey(['tenpay.com']);
          const dm = m?.domainPskeyMap;
          pskey = String(dm?.get?.('tenpay.com') || '');
          out.pskeyFromUserApi = pskey.length;
        }
      } catch (e) {
        out.cookieError = String(e);
      }

      // 3) HTTP 猜测：带 Cookie 打 hb_pc_*（旧路径常 404，仍记录状态）
      const cookieHeader = [
        `uin=o${selfUin.padStart(10, '0')}`,
        pskey ? `p_skey=${pskey}` : '',
        cookies.skey ? `skey=${cookies.skey}` : '',
        cookies.p_uin ? `p_uin=${cookies.p_uin}` : `p_uin=o${selfUin.padStart(10, '0')}`,
      ]
        .filter(Boolean)
        .join('; ');

      const formBases = [
        {
          listid: fullBill,
          authkey: authKey,
          groupid: groupId,
          grouptype: '1',
          channel: String(channel),
          bus_type: String(busType),
          name: selfNick,
          uin: selfUin,
        },
        {
          listid: fullBill,
          authkey: authKey,
          s_index: stringIndex,
          pcbody: pcBody,
          groupid: groupId,
          channel: String(channel),
          bus_type: String(busType),
        },
      ];
      const urls = [
        'https://myun.tenpay.com/cgi-bin/clientv1.0/hb_pc_detail.cgi',
        'https://myun.tenpay.com/cgi-bin/hongbao/hb_pc_detail.cgi',
        'https://tenpay.com/cgi-bin/clientv1.0/hb_pc_detail.cgi',
        `https://htdata2.qq.com/cgi-bin/httpconn?htcmd=hb_pc_detail&uin=${selfUin}`,
      ];
      const httpTries: unknown[] = [];
      for (const url of urls) {
        for (const form of formBases.slice(0, 1)) {
          try {
            const body = new URLSearchParams(form as Record<string, string>).toString();
            const rsp = await fetch(url, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                Cookie: cookieHeader,
                'User-Agent': 'Mozilla/5.0 QQ/9.9.33',
              },
              body,
              signal: AbortSignal.timeout(8000),
            });
            const text = await rsp.text();
            httpTries.push({
              url,
              status: rsp.status,
              bodyHead: text.slice(0, 240).replace(/\s+/g, ' '),
            });
          } catch (e) {
            httpTries.push({ url, error: String(e) });
          }
        }
      }
      out.httpTries = httpTries;

      // 4) MsgService.sendSsoCmdReqByContend(SsoGetToken)
      const msgService = getMsgService(ctx);
      try {
        if (typeof msgService?.sendSsoCmdReqByContend === 'function') {
          const sso = await Promise.race([
            msgService.sendSsoCmdReqByContend('trpc.qqhb.hbpanel.Hongbao.SsoGetToken', ''),
            sleep(8000).then(() => null),
          ]);
          out.ssoGetToken = sso
            ? {
                keys: Object.keys(sso as object).slice(0, 20),
                result: (sso as any)?.result,
                errMsg: (sso as any)?.errMsg,
                rspHead: String((sso as any)?.rsp || '').slice(0, 200),
              }
            : { error: 'timeout/null' };
        } else {
          out.ssoGetToken = { error: 'sendSsoCmdReqByContend unavailable' };
        }
      } catch (e) {
        out.ssoGetToken = { error: String(e) };
      }

      // 5) pullDetail + channel/busType（对照名单长度）
      try {
        const base = {
          recvUin: selfUin,
          recvType: 2,
          peerUid: groupId,
          name: selfNick,
          pcBody: rec.pcBody,
          wishing: rec.wishing || '',
          msgSeq: rec.msgSeq || '',
          index: authKey || stringIndex,
          billNo: fullBill,
          authkey: authKey,
          authKey,
          groupid: groupId,
          grouptype: 1,
          listid: fullBill,
          listId: fullBill,
          channel,
          busType,
          bus_type: busType,
        };
        const raw = await Promise.race([
          msgService.pullDetail({ pullDetailReq: base }),
          sleep(8000).then(() => null),
        ]);
        const rsp = (raw as any)?.pullDetailRsp || raw;
        const list = extractRecvListFromRaw(raw);
        out.pullWithChannel = {
          valid: isValidPullDetailRaw(raw),
          recvNum: rsp?.sendOrder?.recvNum,
          channel: rsp?.sendOrder?.channel,
          busType: rsp?.sendOrder?.busType,
          recvListLen: list.length,
          sample: list[0] || null,
        };
      } catch (e) {
        out.pullWithChannel = { error: String(e) };
      }

      // 6) PacketBackend 裸发 SsoGetToken（若可用）
      try {
        const packetApi = ctx.core?.apis?.PacketApi;
        const client = packetApi?.pkt?.client || packetApi?.pkt?._client;
        out.packetAvailable = !!(packetApi?.available ?? packetApi?.packetStatus);
        if (client?.sendPacket && packetApi?.packetStatus) {
          const buf = Buffer.alloc(0);
          const rsp = await Promise.race([
            client.sendPacket('trpc.qqhb.hbpanel.Hongbao.SsoGetToken', buf, true, 8000),
            sleep(9000).then(() => null),
          ]);
          out.packetSso = rsp
            ? {
                type: typeof rsp,
                keys: rsp && typeof rsp === 'object' ? Object.keys(rsp).slice(0, 15) : [],
                head: Buffer.isBuffer(rsp)
                  ? rsp.slice(0, 64).toString('hex')
                  : String((rsp as any)?.hex_data || (rsp as any)?.data || rsp).slice(0, 120),
              }
            : { error: 'timeout/null' };
        }
      } catch (e) {
        out.packetSso = { error: String(e) };
      }

      ok(res, out);
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：列出 MsgService 可用方法 */
  router.getNoAuth('/debug/services', (_req: any, res: any) => {
    try {
      const session = ctx.core?.context?.session;
      const msgService = session?.getMsgService?.();
      const msgMethods = msgService
        ? Object.getOwnPropertyNames(Object.getPrototypeOf(msgService)).filter(
            (k) => typeof (msgService as any)[k] === 'function' && k !== 'constructor'
          )
        : [];
      const sessionMethods = session
        ? Object.getOwnPropertyNames(Object.getPrototypeOf(session)).filter(
            (k) => k.startsWith('get') && typeof (session as any)[k] === 'function'
          )
        : [];
      const bizMethods: Record<string, string[]> = {};
      for (const name of [
        'getLiteBusinessService',
        'getBizKitService',
        'getMiniAppService',
        'getTicketService',
        'getApiSixService',
        'getMSFService',
        'getCollectionService',
      ]) {
        try {
          const svc = session?.[name]?.();
          if (!svc) continue;
          bizMethods[name] = Object.getOwnPropertyNames(Object.getPrototypeOf(svc))
            .filter((k) => typeof (svc as any)[k] === 'function' && k !== 'constructor')
            .sort();
        } catch {
          /* ignore */
        }
      }
      ok(res, {
        qqVersion: String(ctx.core?.context?.basicInfoWrapper?.getFullQQVersion?.() || ''),
        sessionMethods,
        msgMethods: msgMethods.sort(),
        bizMethods,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：PacketBackend 状态 + 监听 pullDetail 期间的收发包 */
  router.getNoAuth('/debug/packet_sniff', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      const packetApi = ctx.core?.apis?.PacketApi || ctx.core?.packet;
      const pkt = packetApi?.pkt || ctx.core?.context?.packetClient || null;
      const handler =
        ctx.core?.context?.packetHandler ||
        packetApi?.pkt?.client ||
        pkt?._client ||
        null;
      const info: Record<string, unknown> = {
        hasPacketApi: !!packetApi,
        packetAvailable: !!(packetApi?.available ?? pkt?.available),
        packetStatus: packetApi?.packetStatus,
        handlerKeys: handler ? Object.keys(handler).slice(0, 40) : [],
        packetApiKeys: packetApi ? Object.keys(packetApi).slice(0, 40) : [],
      };
      const packets: Array<{ type: string; cmd: string; seq?: number; len?: number }> = [];
      let unsub: (() => void) | null = null;
      try {
        const onAll =
          handler?.onAll?.bind(handler) ||
          handler?.onRecv?.bind(handler) ||
          pkt?.client?.onAll?.bind(pkt.client);
        if (typeof onAll === 'function') {
          unsub = onAll((data: any) => {
            packets.push({
              type: String(data?.type ?? ''),
              cmd: String(data?.cmd || ''),
              seq: data?.seq,
              len: String(data?.hex_data || '').length / 2,
            });
          });
        } else if (handler?.onCmd) {
          // fallback: no-op marker
          info.listenMode = 'onCmd-only-unavailable-for-wildcard';
        } else {
          info.listenMode = 'no-listener';
        }
      } catch (e) {
        info.listenError = String(e);
      }

      let detail: unknown = null;
      if (rec?.pcBody) {
        try {
          detail = await pullDetail(ctx, {
            ...rec,
            billNo: rec.billNo,
            pcBody: rec.pcBody,
            stringIndex: rec.stringIndex,
            authKey: rec.authKey,
          } as any);
        } catch (e) {
          info.pullError = String(e);
        }
      }
      try {
        unsub?.();
      } catch {
        /* ignore */
      }
      const d = detail as any;
      ok(res, {
        info,
        packetCount: packets.length,
        packets: packets.slice(0, 80),
        recvListLen: d ? extractRecvListFromRaw(d.raw || d).length : 0,
        claimsLen: Array.isArray(d?.claims) ? d.claims.length : 0,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：扫描 wallet meta + 强制 refresh pullDetail */
  router.getNoAuth('/debug/refresh_detail', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec) return fail(res, '缺少 bill_no 或本地无记录');
      const meta = await scanWalletMetaFromHistory(ctx, rec);
      const updated = await refreshRecordDetail(ctx, {
        ...rec,
        pcBody: meta.pcBody || rec.pcBody,
        stringIndex: meta.stringIndex || rec.stringIndex,
        authKey: meta.authKey,
        wishing: meta.wishing || rec.wishing,
      });
      ok(res, {
        meta,
        claims: updated.claims,
        summary: updated.summary,
        recvListLen: extractRecvListFromRaw(updated.rawDetail).length,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：从群消息读取 walletElement 原始字段 */
  router.getNoAuth('/debug/wallet', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec) return fail(res, '缺少 bill_no 或本地无记录');
      const msgApi = ctx.core?.apis?.MsgApi;
      const peer = { chatType: 2, peerUid: String(rec.groupId), guildId: '' };
      let walletEl: unknown = null;
      let freshPcBody = rec.pcBody;
      if (msgApi?.getMsgsBySeqAndCount && rec.msgSeq) {
        const rsp = await msgApi.getMsgsBySeqAndCount(peer, String(rec.msgSeq), 1, true, true);
        for (const raw of rsp?.msgList || []) {
          const w = extractWalletFromRaw(raw);
          if (w?.billNo === rec.billNo) {
            walletEl = w.walletElement;
            freshPcBody = w.pcBody || freshPcBody;
            break;
          }
        }
      }
      ok(res, {
        billNo: rec.billNo,
        storedPcBodyPrefix: String(rec.pcBody || '').slice(0, 80),
        freshPcBodyPrefix: String(freshPcBody || '').slice(0, 80),
        pcBodySame: rec.pcBody === freshPcBody,
        walletElement: walletEl,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：穷举 pullDetail 参数组合 */
  router.getNoAuth('/debug/pull_exhaustive', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec) return fail(res, '缺少 bill_no 或本地无记录');
      const meta = await scanWalletMetaFromHistory(ctx, rec);
      const authKey = sanitizeAuthKey(meta.authKey || rec.authKey || '');
      const stringIndex = rec.stringIndex || meta.stringIndex || '';
      const wallet = walletFromRecord({
        ...rec,
        pcBody: meta.pcBody || rec.pcBody,
        stringIndex,
        authKey,
        wishing: meta.wishing || rec.wishing,
      });
      const msgService = getMsgService(ctx);
      const selfUin = String(ctx.core?.selfInfo?.uin || '');
      const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
      const peerUin = String(wallet.peerUin || wallet.peerUid || '');
      const peerUid = String(wallet.peerUid || peerUin);
      const chatType = Number(wallet.chatType ?? 2);
      const extraTries: Array<{ label: string; score: number; recvListLen: number; recvNum: string; error?: string }> =
        [];
      if (msgService?.pullDetail && wallet.pcBody) {
        const wishingVariants = [
          ['wish_title', wallet.wishing || ''],
          ['wish_index', stringIndex],
          ['wish_auth', authKey],
          ['wish_empty', ''],
        ] as const;
        for (const [wlabel, wishing] of wishingVariants) {
          for (const index of [stringIndex, authKey].filter(Boolean)) {
            const base = {
              recvUin: selfUin,
              recvType: chatType,
              peerUid,
              name: selfNick,
              pcBody: wallet.pcBody,
              wishing,
              msgSeq: wallet.msgSeq || '',
              index,
              billNo: wallet.billNo,
              authkey: authKey,
              authKey,
              groupid: peerUin,
              grouptype: 1,
              listid: wallet.billNo,
              listId: wallet.billNo,
            };
            try {
              const ret = await Promise.race([
                msgService.pullDetail(base),
                sleep(8000).then(() => null),
              ]);
              if (!ret) {
                extraTries.push({
                  label: `wish/${wlabel}/${String(index).slice(0, 8)}`,
                  score: -1,
                  recvListLen: 0,
                  recvNum: '0',
                  error: 'timeout',
                });
                continue;
              }
              const rsp = (ret as any)?.pullDetailRsp || ret;
              extraTries.push({
                label: `wish/${wlabel}/${String(index).slice(0, 8)}`,
                score: scorePullDetailRaw(ret),
                recvListLen: extractRecvListFromRaw(ret).length,
                recvNum: String(rsp?.sendOrder?.recvNum ?? '0'),
              });
            } catch (e) {
              extraTries.push({
                label: `wish/${wlabel}/${String(index).slice(0, 8)}`,
                score: -1,
                recvListLen: 0,
                recvNum: '0',
                error: String(e),
              });
            }
          }
        }
      }
      const tries = await exhaustPullDetailAttempts(ctx, wallet, 24);
      const merged = [...extraTries, ...tries].sort((a, b) => b.score - a.score || b.recvListLen - a.recvListLen);
      ok(res, { billNo: rec.billNo.slice(-8), meta: { ...meta, authKey }, tries: merged.slice(0, 24) });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：用内核日志同名字段拉领取名单（offset/limit + bytes_pc_authkey） */
  router.getNoAuth('/debug/pull_list_native', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec?.pcBody) return fail(res, '缺少 bill_no 或 pcBody');
      const meta = await scanWalletMetaFromHistory(ctx, rec);
      const authKey = sanitizeAuthKey(meta.authKey || rec.authKey || '');
      const stringIndex = String(meta.stringIndex || rec.stringIndex || '');
      const msgService = getMsgService(ctx);
      if (!msgService?.pullDetail) return fail(res, 'MsgService 不可用');
      const selfUin = String(ctx.core?.selfInfo?.uin || '');
      const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
      const groupId = String(rec.groupId || '');
      const pcBody = meta.pcBody || rec.pcBody;
      const fullBill = rec.billNo;
      const channel = Number(rec.redChannel || 1) || 1;
      const recvNum = Number(rec.summary?.recvNum || 20) || 20;

      const bases: Array<[string, Record<string, unknown>]> = [];
      for (const index of [authKey, stringIndex].filter(Boolean)) {
        for (const recvUin of [groupId, selfUin]) {
          const common = {
            recvUin,
            recvType: 2,
            peerUid: groupId,
            name: selfNick,
            pcBody,
            wishing: rec.wishing || '',
            msgSeq: rec.msgSeq || '',
            index,
            billNo: fullBill,
            channel,
            busType: 2,
            // 内核日志：pull detail info, uint64_groupid / offset / limit / bytes_pc_authkey / bytes_pc_listid / grouptype
            uint64_groupid: groupId,
            groupid: groupId,
            groupId,
            int32_offset: 0,
            int32_limit: Math.max(recvNum, 20),
            offset: 0,
            limit: Math.max(recvNum, 20),
            count: Math.max(recvNum, 20),
            bytes_pc_authkey: authKey,
            bytes_pc_listid: fullBill,
            authkey: authKey,
            authKey,
            listid: fullBill,
            listId: fullBill,
            uint32_grouptype: 1,
            grouptype: 1,
            uint32_channel: channel,
            uint32_bus_type: 2,
          };
          bases.push([`native/${recvUin}/${String(index).slice(0, 8)}`, common]);
        }
      }

      const out: unknown[] = [];
      for (const [label, base] of bases) {
        for (const [wrap, payload] of [
          ['flat', base],
          ['req', { pullDetailReq: base }],
          ['req+qt1', { pullDetailReq: { ...base, queryType: 1 } }],
          ['req+page', { pullDetailReq: { ...base, int32_offset: 0, int32_limit: 150, offset: 0, limit: 150 } }],
        ] as const) {
          try {
            const raw = await Promise.race([
              msgService.pullDetail(payload),
              sleep(8000).then(() => null),
            ]);
            if (!raw) {
              out.push({ label: `${label}/${wrap}`, error: 'timeout' });
              continue;
            }
            const list = extractRecvListFromRaw(raw);
            const rsp = (raw as any)?.pullDetailRsp || raw;
            out.push({
              label: `${label}/${wrap}`,
              valid: isValidPullDetailRaw(raw),
              recvListLen: list.length,
              recvNum: rsp?.sendOrder?.recvNum,
              sample: list[0] || null,
              sampleKeys: list[0] ? Object.keys(list[0]).slice(0, 12) : [],
            });
            if (list.length) {
              // early exit with success
              return ok(res, { billNo: fullBill, hit: `${label}/${wrap}`, list, out });
            }
          } catch (e) {
            out.push({ label: `${label}/${wrap}`, error: String(e) });
          }
        }
      }
      ok(res, { billNo: fullBill, authKey: authKey.slice(0, 8), out });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：按 grab 同形 + wishing=authKey 等最小组合试 pullDetail */
  router.getNoAuth('/debug/pull_minimal', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec?.pcBody) return fail(res, '缺少 bill_no 或 pcBody');
      const meta = await scanWalletMetaFromHistory(ctx, rec);
      const authKey = String(meta.authKey || rec.authKey || '').replace(/[^0-9a-fA-F]/g, '').slice(0, 32);
      const stringIndex = meta.stringIndex || rec.stringIndex;
      const msgService = getMsgService(ctx);
      if (!msgService?.pullDetail) return fail(res, 'MsgService 不可用');
      const selfUin = String(ctx.core?.selfInfo?.uin || '');
      const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
      const peerUin = String(rec.groupId || '');
      const peerUid = peerUin;
      const out: unknown[] = [];
      const variants: Array<[string, Record<string, unknown>]> = [
        [
          'grab-shape',
          {
            recvUin: peerUin,
            recvType: 2,
            peerUid,
            name: selfNick,
            pcBody: meta.pcBody || rec.pcBody,
            wishing: rec.wishing || '',
            msgSeq: rec.msgSeq || '',
            index: stringIndex,
          },
        ],
        [
          'wishing=authKey',
          {
            recvUin: peerUin,
            recvType: 2,
            peerUid,
            name: selfNick,
            pcBody: meta.pcBody || rec.pcBody,
            wishing: authKey,
            msgSeq: rec.msgSeq || '',
            index: stringIndex,
          },
        ],
        [
          'wishing=authKey index=authKey',
          {
            recvUin: peerUin,
            recvType: 2,
            peerUid,
            name: selfNick,
            pcBody: meta.pcBody || rec.pcBody,
            wishing: authKey,
            msgSeq: rec.msgSeq || '',
            index: authKey,
            authkey: authKey,
            listid: rec.billNo,
            groupid: peerUin,
            grouptype: 1,
          },
        ],
        [
          'selfUin+authKey',
          {
            recvUin: selfUin,
            recvType: 2,
            peerUid,
            name: selfNick,
            pcBody: meta.pcBody || rec.pcBody,
            wishing: authKey,
            msgSeq: rec.msgSeq || '',
            index: authKey,
            authkey: authKey,
            listid: rec.billNo,
            groupid: peerUin,
            grouptype: 1,
          },
        ],
        [
          'sendUin+stringIndex',
          {
            recvUin: String(rec.senderUin || peerUin),
            recvType: 2,
            peerUid,
            name: String(rec.senderName || selfNick),
            pcBody: meta.pcBody || rec.pcBody,
            wishing: rec.wishing || '',
            msgSeq: rec.msgSeq || '',
            index: stringIndex,
          },
        ],
      ];
      for (const [label, base] of variants) {
        for (const [wrap, payload] of [
          ['flat', base],
          ['req', { pullDetailReq: base }],
        ] as const) {
          try {
            const raw = await Promise.race([
              msgService.pullDetail(payload),
              sleep(8000).then(() => null),
            ]);
            if (!raw) {
              out.push({ label: `${label}/${wrap}`, error: 'timeout' });
              continue;
            }
            const list = extractRecvListFromRaw(raw);
            const rsp = (raw as any)?.pullDetailRsp || raw;
            out.push({
              label: `${label}/${wrap}`,
              valid: isValidPullDetailRaw(raw),
              recvListLen: list.length,
              recvNum: rsp?.sendOrder?.recvNum,
              sample: list[0] || null,
            });
          } catch (e) {
            out.push({ label: `${label}/${wrap}`, error: String(e) });
          }
        }
      }
      ok(res, { billNo: rec.billNo.slice(-8), authKey, stringIndex, out });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：落盘一次完整 pullDetail 原始结构（找名单字段） */
  router.getNoAuth('/debug/dump_raw', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec?.pcBody) return fail(res, '缺少 bill_no 或 pcBody');
      const detail = await pullDetail(ctx, walletFromRecord(rec));
      const raw = (detail as any)?.raw ?? detail;
      const seen = new WeakSet();
      const walk = (v: any, path: string, out: string[], depth: number) => {
        if (depth > 6 || out.length > 400) return;
        if (v && typeof v === 'object') {
          if (seen.has(v)) return;
          seen.add(v);
          if (Array.isArray(v)) {
            out.push(`${path}: Array(${v.length})`);
            if (v.length && depth < 5) walk(v[0], `${path}[0]`, out, depth + 1);
            return;
          }
          for (const k of Object.keys(v)) {
            const child = v[k];
            const p = path ? `${path}.${k}` : k;
            if (child == null || typeof child !== 'object') {
              const s = String(child);
              out.push(`${p}=${s.length > 80 ? s.slice(0, 80) + '…' : s}`);
            } else {
              walk(child, p, out, depth + 1);
            }
          }
        }
      };
      const paths: string[] = [];
      walk(raw, '', paths, 0);
      const list = extractRecvListFromRaw(raw);
      ok(res, {
        billNo: rec.billNo,
        recvListLen: list.length,
        paths,
        listSample: list.slice(0, 5),
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：等待 QQ 客户端点开红包详情时的 pullDetail 捕获（含名单） */
  router.getNoAuth('/debug/wait_capture', async (req: any, res: any) => {
    try {
      const timeoutMs = Math.min(Number(req.query?.timeout || 90000) || 90000, 180000);
      const onlyList = String(req.query?.only_list || '1') !== '0';
      const file = path.join(String(ctx.dataPath || ''), 'pullDetail_captures.json');
      const start = Date.now();
      let lastLen = 0;
      try {
        if (fs.existsSync(file)) {
          const arr = JSON.parse(fs.readFileSync(file, 'utf-8'));
          lastLen = Array.isArray(arr) ? arr.length : 0;
        }
      } catch {
        lastLen = 0;
      }
      const deadline = start + timeoutMs;
      while (Date.now() < deadline) {
        await sleep(800);
        if (!fs.existsSync(file)) continue;
        let arr: any[] = [];
        try {
          arr = JSON.parse(fs.readFileSync(file, 'utf-8'));
        } catch {
          continue;
        }
        if (!Array.isArray(arr) || arr.length <= lastLen) continue;
        const fresh = arr.slice(lastLen);
        lastLen = arr.length;
        const hit = onlyList
          ? fresh.find((e) => Number(e?.recvListLen || 0) > 0)
          : fresh[fresh.length - 1];
        if (hit) {
          return ok(res, {
            waitedMs: Date.now() - start,
            hit,
            freshCount: fresh.length,
          });
        }
      }
      ok(res, { waitedMs: Date.now() - start, hit: null, tip: '超时未捕获到含名单的 pullDetail' });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 调试：尝试多种 pullDetail / 钱包接口 */
  router.getNoAuth('/debug/pull_try', async (req: any, res: any) => {
    try {
      const billNo = String(req.query?.bill_no || req.query?.billNo || '');
      let rec = billNo ? getRecord(billNo) : null;
      if (!rec && billNo) {
        rec = Array.from(records.values()).find((r) => r.billNo.endsWith(billNo)) || null;
      }
      if (!rec?.pcBody) return fail(res, '缺少 bill_no 或 pcBody');
      const msgService = getMsgService(ctx);
      if (!msgService) return fail(res, 'MsgService 不可用');
      const wallet = walletFromRecord(rec);
      const selfUin = String(ctx.core?.selfInfo?.uin || '');
      const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
      const chatType = Number(wallet.chatType ?? 2);
      const peerUin = String(wallet.peerUin || wallet.peerUid || '');
      const peerUid = String(wallet.peerUid || peerUin);
      const recvUin = chatType === 1 ? selfUin : peerUin;
      const base = {
        recvUin,
        recvType: chatType,
        peerUid,
        name: selfNick,
        pcBody: wallet.pcBody,
        wishing: wallet.wishing || '',
        msgSeq: wallet.msgSeq || '',
        index: wallet.stringIndex,
        billNo: wallet.billNo,
      };
      const tries: Array<[string, () => Promise<unknown>]> = [
        ['pullDetail', () => msgService.pullDetail(base)],
        ['pullDetail+selfUin', () => msgService.pullDetail({ pullDetailReq: { ...base, recvUin: selfUin } })],
        ['pullDetail+queryType1', () => msgService.pullDetail({ pullDetailReq: { ...base, queryType: 1, offset: 0, count: 150 } })],
        ['pullDetail+queryType1+self', () => msgService.pullDetail({ pullDetailReq: { ...base, recvUin: selfUin, queryType: 1, offset: 0, count: 150 } })],
        ['pullDetail+queryRecvList', () => msgService.pullDetail({ pullDetailReq: { ...base, queryRecvList: 1 } })],
        ['pullDetail+needRecvList', () => msgService.pullDetail({ pullDetailReq: { ...base, needRecvList: true } })],
        ['pullDetailFull(api)', () => pullDetail(ctx, walletFromRecord(rec))],
      ];
      if (typeof msgService.pullRedBagPasswordList === 'function') {
        tries.push(['pullRedBagPasswordList()', () => msgService.pullRedBagPasswordList()]);
      }
      const out: unknown[] = [];
      for (const [label, fn] of tries) {
        try {
          const timeoutMs = label.includes('Full') ? 120000 : 10000;
          const raw = await Promise.race([fn(), sleep(timeoutMs).then(() => null)]);
          if (!raw) {
            out.push({ label, error: 'timeout' });
            continue;
          }
          const detailRaw = (raw as any)?.raw ?? raw;
          const rsp = (detailRaw as any)?.pullDetailRsp || detailRaw;
          const list = extractRecvListFromRaw(detailRaw);
          out.push({
            label,
            valid: isValidPullDetailRaw(detailRaw),
            recvListLen: list.length,
            claimsLen: Array.isArray((raw as any)?.claims) ? (raw as any).claims.length : 0,
            recvNum: rsp?.sendOrder?.recvNum,
            keys: Object.keys(rsp || {}),
            sample: list.length ? list[0] : (raw as any)?.claims?.[0] || null,
          });
        } catch (e) {
          out.push({ label, error: String(e) });
        }
      }
      ok(res, { billNo, out });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 健康检查 */
  router.getNoAuth('/status', (_req: any, res: any) => {
    ok(res, {
      enabled: config.enabled,
      autoGrab: config.autoGrab,
      autoPullDetail: config.autoPullDetail,
      watchGroups: config.watchGroups,
      recordCount: records.size,
      selfUin: String(ctx.core?.selfInfo?.uin || ''),
    });
  });

  /** 发红包：仅占位说明（需手动在 QQ 客户端发） */
  router.postNoAuth('/send', (_req: any, res: any) => {
    ok(res, {
      supported: false,
      tip: '发红包请在 QQ 电脑端手动发送。本插件只实现收红包与查领取详情。',
    });
  });

  /**
   * 收红包
   * body: 可不传 —— 则仅说明自动模式；或传 billNo 对已缓存红包补领
   */
  router.postNoAuth('/receive', async (req: any, res: any) => {
    try {
      const body = (req.body || {}) as Record<string, unknown>;
      const billNo = String(body.billNo || '');
      if (!billNo) {
        return ok(res, {
          tip: '实时收红包由插件监听消息后自动调用 grabRedBag。也可传 billNo 对已记录红包补领。',
          autoGrab: config.autoGrab,
        });
      }
      const rec = getRecord(billNo);
      if (!rec?.pcBody) return fail(res, '未找到该红包缓存，或缺少 pcBody，无法补领');
      const wallet = walletFromRecord(rec);
      if (config.handlePassword) await sendPasswordIfNeeded(ctx, wallet);
      await sleep(randomDelay(config.delayMin, config.delayMax));
      const grab = await grabRedBag(ctx, wallet);
      upsertRecord({
        ...rec,
        grabbed: true,
        myAmount: grab.amount || rec.myAmount,
        claims: rec.claims,
        updatedAt: Date.now(),
      });
      ok(res, { billNo, grab });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /**
   * 回扫群最近消息里的红包并按当前配置自动领取
   * body: group_id（必填）, count（默认 30）
   */
  router.postNoAuth('/backfill', async (req: any, res: any) => {
    try {
      const body = (req.body || {}) as Record<string, unknown>;
      const groupId = String(body.group_id || body.groupId || req.query?.group_id || '');
      const count = Math.min(
        80,
        Math.max(5, parseInt(String(body.count || req.query?.count || '30'), 10) || 30)
      );
      if (!groupId) return fail(res, '缺少 group_id');
      if (config.watchGroups.length && !config.watchGroups.includes(groupId)) {
        return fail(res, `群 ${groupId} 不在监听列表，请先同步 watchGroups`);
      }
      const msgApi = ctx.core?.apis?.MsgApi;
      const peer = { chatType: 2, peerUid: groupId, guildId: '' };
      let msgList: any[] = [];
      if (typeof msgApi?.getAioFirstViewLatestMsgs === 'function') {
        const rsp = await msgApi.getAioFirstViewLatestMsgs(peer, count);
        msgList = rsp?.msgList || [];
      } else if (typeof msgApi?.getMsgHistory === 'function') {
        const rsp = await msgApi.getMsgHistory(peer, '0', count, true);
        msgList = rsp?.msgList || [];
      } else if (typeof msgApi?.getMsgs === 'function') {
        const rsp = await msgApi.getMsgs(peer, '', count, true, true);
        msgList = rsp?.msgList || [];
      } else {
        return fail(res, 'MsgApi 无可用历史拉取方法');
      }
      const found: string[] = [];
      const grabbed: Array<{ billNo: string; amount?: number; error?: string }> = [];
      for (const raw of msgList) {
        const w = extractWalletFromRaw(raw);
        if (!w?.billNo || !w.pcBody) continue;
        found.push(w.billNo);
        await onRawRedPacketMessage(ctx, raw);
        const rec = getRecord(w.billNo);
        if (rec?.grabbed) {
          grabbed.push({ billNo: w.billNo, amount: rec.myAmount });
        } else if (rec && !rec.grabbed && config.autoGrab) {
          try {
            if (config.handlePassword) await sendPasswordIfNeeded(ctx, walletFromRecord(rec));
            await sleep(randomDelay(config.delayMin, config.delayMax));
            const grab = await grabRedBag(ctx, walletFromRecord(rec));
            upsertRecord({
              ...rec,
              grabbed: true,
              myAmount: grab.amount || rec.myAmount,
              updatedAt: Date.now(),
            });
            grabbed.push({ billNo: w.billNo, amount: grab.amount });
          } catch (e) {
            grabbed.push({ billNo: w.billNo, error: String(e) });
          }
        }
      }
      ok(res, {
        groupId,
        scanned: msgList.length,
        foundBills: [...new Set(found)],
        grabbed,
        watchGroups: config.watchGroups,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /**
   * 查红包领取详情（核心需求）
   * query/body:
   *   group_id  群号（必填）
   *   start     开始时间（unix秒 或 2026-03-26 12:00:00）
   *   end       结束时间
   *   refresh   是否对匹配到的红包再调一次 pullDetail（默认 true）
   */
  router.getNoAuth('/query', handleQuery(ctx));
  router.postNoAuth('/query', handleQuery(ctx));

  /** 按 billNo 强制拉详情 */
  router.postNoAuth('/detail', async (req: any, res: any) => {
    try {
      const billNo = String(req.body?.billNo || '');
      if (!billNo) return fail(res, '缺少 billNo');
      const rec = getRecord(billNo);
      if (!rec) return fail(res, '本地无此红包记录，请先在群内出现过该红包消息');
      const next = await refreshRecordDetail(ctx, rec);
      ok(res, formatQueryItem(next));
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 列出本地缓存（默认不自动 pullDetail，避免历史/监控刷新卡死） */
  router.getNoAuth('/list', async (req: any, res: any) => {
    const groupId = req.query?.group_id ? String(req.query.group_id) : '';
    const refreshIncomplete = String(req.query?.refresh_incomplete ?? 'false') === 'true';
    let list = Array.from(records.values());
    if (groupId) list = list.filter((r) => String(r.groupId) === groupId);
    list.sort((a, b) => b.msgTime - a.msgTime);

    if (refreshIncomplete) {
      const top = list.slice(0, 5);
      for (const rec of top) {
        if (!needsDetailRefresh(rec)) continue;
        try {
          await Promise.race([
            refreshRecordDetail(ctx, rec),
            sleep(12000).then(() => null),
          ]);
          await sleep(200);
        } catch {
          /* 保留旧缓存 */
        }
      }
      list = groupId
        ? Array.from(records.values()).filter((r) => String(r.groupId) === groupId)
        : Array.from(records.values());
      list.sort((a, b) => b.msgTime - a.msgTime);
    }

    ok(
      res,
      list.slice(0, 100).map(formatQueryItem)
    );
  });

  /** 核查群：群资料 + 成员名单 */
  router.getNoAuth('/members', async (req: any, res: any) => {
    try {
      const groupId = String(req.query?.group_id || req.query?.groupId || '').trim();
      if (!groupId) return fail(res, '缺少 group_id（请填写要核查的群号）');
      const noCache = String(req.query?.no_cache ?? 'true') !== 'false';

      let group: Record<string, unknown> = { group_id: groupId };
      try {
        const infoRaw = await ctx.actions.call(
          'get_group_info',
          { group_id: groupId, no_cache: noCache },
          ctx.adapterName,
          ctx.pluginManager.config
        );
        const info = unwrapActionData(infoRaw) as Record<string, unknown> | null;
        if (info && typeof info === 'object') group = { ...group, ...info };
      } catch (e) {
        group.info_error = String(e);
      }

      const listRaw = await ctx.actions.call(
        'get_group_member_list',
        { group_id: groupId, no_cache: noCache },
        ctx.adapterName,
        ctx.pluginManager.config
      );
      const list = unwrapActionList(listRaw);
      const members = list.map(normalizeMember).sort((a, b) => {
        const rank = (r: string) => (r === 'owner' ? 0 : r === 'admin' ? 1 : 2);
        const d = rank(a.role) - rank(b.role);
        if (d !== 0) return d;
        return a.uin.localeCompare(b.uin);
      });

      ok(res, {
        group_id: groupId,
        group_name: String(group.group_name || group.groupName || ''),
        member_count: Number(group.member_count || group.memberCount || members.length || 0),
        max_member_count: Number(group.max_member_count || group.maxMemberCount || 0),
        members,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  /** 更新配置 */
  router.postNoAuth('/config', (req: any, res: any) => {
    const body = req.body || {};
    if (body.clearRecords === true) {
      const cleared = clearAllRecords();
      ok(res, { ...config, cleared });
      return;
    }
    Object.assign(config, body);
    if (typeof body.watchGroups === 'string') {
      config.watchGroups = String(body.watchGroups)
        .split(/[,，\s]+/)
        .map((s: string) => s.trim())
        .filter(Boolean);
    }
    saveConfig();
    ok(res, config);
  });

  router.getNoAuth('/config', (_req: any, res: any) => ok(res, config));

  /** 清空所有监控记录 */
  router.postNoAuth('/clear', (_req: any, res: any) => {
    const cleared = clearAllRecords();
    ok(res, { cleared });
  });
}

function handleQuery(ctx: NapCatPluginContext) {
  return async (req: any, res: any) => {
    try {
      const q = { ...(req.query || {}), ...(req.body || {}) };
      const groupId = String(q.group_id || q.groupId || '');
      if (!groupId) return fail(res, '缺少 group_id');

      const now = Math.floor(Date.now() / 1000);
      const start = parseTimeBound(q.start ?? q.start_time, now - 7 * 86400);
      const end = parseTimeBound(q.end ?? q.end_time, now + 60);
      // 默认不强制刷新；GUI 勾选时再拉。避免穷举 pullDetail 把查询卡死
      const refresh = String(q.refresh ?? 'false') === 'true';

      let matched = queryByGroupAndTime(groupId, start, end);

      if (refresh) {
        for (const rec of matched.slice(0, 30)) {
          if (!rec.pcBody) continue;
          try {
            await Promise.race([
              refreshRecordDetail(ctx, rec),
              sleep(15000).then(() => null),
            ]);
          } catch {
            /* 保留旧缓存 */
          }
          await sleep(200);
        }
        matched = queryByGroupAndTime(groupId, start, end);
      }

      ok(res, {
        group_id: groupId,
        start,
        end,
        start_text: formatTime(start),
        end_text: formatTime(end),
        count: matched.length,
        packets: matched.map(formatQueryItem),
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  };
}

export function formatQueryItem(r: RedPacketRecord) {
  const summary = r.summary || parseSendOrderSummary(r.rawDetail);
  // 以本地 claims 为准；rawDetail 只补金额/人数，不再用串号 recvName 覆盖昵称
  let claims = repairBadClaims({ ...r, summary });
  if (r.rawDetail) {
    const fromRaw = normalizeClaims(r.rawDetail);
    // normalizeClaims 已清空「全员同名」；只合并 uin/金额/时间
    claims = mergeClaims(
      claims,
      fromRaw.map((c) => ({
        ...c,
        // 若本地已有该 uin 的昵称，mergeClaims 会保留；此处再保险：无本地时也不填串名
        name: c.name,
      }))
    );
    claims = inferMissingAmounts(claims, summary);
    claims = repairBadClaims({ claims, summary, rawDetail: r.rawDetail });
  } else if (summary) {
    claims = repairBadClaims({ claims, summary });
  }

  // 仍全员同名：直接丢掉昵称，界面至少显示正确 QQ
  if (claims.length >= 2) {
    const names = claims.map((c) => String(c.name || '').trim()).filter(Boolean);
    if (names.length >= 2 && names.every((n) => n === names[0])) {
      claims = claims.map((c) => ({ ...c, name: '' }));
    }
  }

  const confirmed = claims.filter((c) => {
    const name = String(c.name || '').trim();
    if (name.startsWith('未知领取人')) return false;
    return (c.time || 0) > 0 || (c.amount || 0) > 0 || isValidUin(c.uin);
  });
  const recvNum = summary?.recvNum || 0;
  const missing = recvNum > 0 ? Math.max(0, recvNum - confirmed.length) : 0;
  let displayClaims = [...confirmed];
  let claimsNote = '';
  if (missing > 0) {
    displayClaims.push({
      uin: '',
      name: `另有 ${missing} 人已领但名单未同步（不是「还没抢的份」）`,
      amount: 0,
      time: 0,
      timeText: '',
    });
    claimsNote =
      `已确认 ${confirmed.length} 人 / 汇总已领 ${recvNum} 人。` +
      '剩余名单将在详情补拉或灰条同步后更新。';
  }

  return {
    bill_no: r.billNo,
    group_id: r.groupId,
    group_name: r.groupName,
    sender_uin: r.senderUin,
    sender_name: r.senderName,
    wishing: r.wishing,
    msg_time: r.msgTime,
    msg_time_text: formatTime(r.msgTime),
    grabbed: r.grabbed,
    my_amount: r.myAmount ?? null,
    claims_note: claimsNote,
    summary: summary
      ? {
          total_num: summary.totalNum,
          recv_num: summary.recvNum,
          total_amount: summary.totalAmount,
          recv_amount: summary.recvAmount,
          lucky_uin: summary.luckyUin,
          lucky_name: summary.luckyName,
        }
      : null,
    /** 领取详情：已确认记录 + 缺失提示行 */
    claims: [...displayClaims]
      .sort((a, b) => (b.time || 0) - (a.time || 0))
      .map((c) => ({
        uin: c.uin,
        name: c.name,
        amount: c.amount,
        time: c.time,
        time_text: c.timeText || formatTime(c.time),
      })),
  };
}

/** 消息到达时：记录 + 可选自动领 + 拉详情 */
export async function onRedPacketMessage(ctx: NapCatPluginContext, event: any) {
  const wallet = extractWallet(event);
  if (!wallet) {
    // 调试：有 raw 但解析失败时打日志
    const raw = event?.raw;
    if (raw?.elements?.some((el: any) => el?.elementType === 9)) {
      ctx.logger?.warn?.(
        '[红包监控] 检测到 ElementType=9 但未能解析 walletElement（可能字段不完整）'
      );
    }
    return;
  }
  await handleWallet(ctx, wallet);
}

/** 内核原始消息入口 */
export async function onRawRedPacketMessage(ctx: NapCatPluginContext, raw: any) {
  const wallet = extractWalletFromRaw(raw);
  if (!wallet) {
    const diag = diagnoseRedPacketRaw(raw);
    if (diag) {
      ctx.logger?.warn?.(
        `[红包监控] 看到红包相关消息但解析失败: ${diag} peer=${raw?.peerUin} msgId=${raw?.msgId}`
      );
    }
    return;
  }
  const fromGray = !!(wallet.walletElement && wallet.walletElement.fromGrayTip);
  await handleWallet(ctx, wallet, { fromGrayTip: fromGray });
}

async function handleWallet(
  ctx: NapCatPluginContext,
  wallet: WalletContext,
  opts: { fromGrayTip?: boolean } = {}
) {
  if (wallet.chatType !== 2 && !opts.fromGrayTip) return; // 本需求聚焦群红包
  // 灰条有时 chatType 不准，用 peerUin 当群号
  if (opts.fromGrayTip && !wallet.peerUin) return;

  if (config.watchGroups.length && !config.watchGroups.includes(String(wallet.peerUin))) {
    return;
  }

  const existing = getRecord(wallet.billNo);

  // 灰条 = 他人/自己领取事件：追加领取人，不抢包（不受 processingBills 阻塞）
  if (opts.fromGrayTip) {
    const grayClaim = extractGrayTipClaim(wallet);
    if (!grayClaim) return;
    try {
      let claims = mergeClaims(existing?.claims || [], [grayClaim]);
      let summary = existing?.summary;
      if (existing?.rawDetail) {
        const combined = combineClaimsFromDetail(claims, existing.rawDetail, undefined, {
          senderUin: existing.senderUin,
          senderName: existing.senderName,
        });
        claims = combined.claims;
        summary = combined.summary;
      } else {
        claims = repairBadClaims({ claims, summary });
      }
      const base: RedPacketRecord = {
        billNo: wallet.billNo,
        groupId: wallet.peerUin,
        groupName: wallet.peerName || existing?.groupName || wallet.peerUin,
        senderUin: wallet.senderUin || existing?.senderUin || '',
        senderName: wallet.senderName || existing?.senderName || '',
        wishing: existing?.wishing || wallet.wishing,
        msgSeq: existing?.msgSeq || wallet.msgSeq,
        msgTime: existing?.msgTime || wallet.msgTime,
        chatType: wallet.chatType || 2,
        peerUid: wallet.peerUid,
        grabbed: existing?.grabbed || false,
        claims,
        updatedAt: Date.now(),
        pcBody: wallet.pcBody || existing?.pcBody,
        stringIndex: wallet.stringIndex || existing?.stringIndex,
        redChannel: wallet.redChannel ?? existing?.redChannel ?? 0,
        summary,
        rawDetail: existing?.rawDetail,
      };
      upsertRecord(base);
      ctx.logger?.info?.(
        `[红包监控] 灰条领取 billNo=${wallet.billNo} ${grayClaim.name || grayClaim.uin}`
      );

      if (config.enabled && config.autoPullDetail && (wallet.pcBody || existing?.pcBody)) {
        try {
          await sleep(600);
          await refreshRecordDetail(ctx, getRecord(wallet.billNo)!);
        } catch (e) {
          ctx.logger?.warn?.('[红包监控] 灰条后 pullDetail 失败', e);
        }
      }
    } catch (e) {
      ctx.logger?.warn?.('[红包监控] 灰条处理失败', e);
    }
    return;
  }

  if (processingBills.has(wallet.billNo)) return;

  // 普通红包消息：已领且已有详情可跳过（避免重复抢）
  if (existing?.grabbed && existing?.rawDetail) return;

  processingBills.add(wallet.billNo);
  try {
    const base: RedPacketRecord = {
      billNo: wallet.billNo,
      groupId: wallet.peerUin,
      groupName: wallet.peerName,
      senderUin: wallet.senderUin,
      senderName: wallet.senderName,
      wishing: wallet.wishing,
      msgSeq: wallet.msgSeq,
      msgTime: wallet.msgTime,
      chatType: wallet.chatType || 2,
      peerUid: wallet.peerUid,
      grabbed: existing?.grabbed || false,
      claims: existing?.claims || [],
      updatedAt: Date.now(),
      pcBody: wallet.pcBody || existing?.pcBody,
      stringIndex: wallet.stringIndex || existing?.stringIndex,
      redChannel: wallet.redChannel,
    };
    upsertRecord(base);
    ctx.logger?.info?.(
      `[红包监控] 发现红包 billNo=${wallet.billNo} 群=${wallet.peerUin} 发送者=${wallet.senderUin}` +
        (opts.fromGrayTip ? ' (来自领取灰条)' : '')
    );

    if (!config.enabled) return;

    const selfUin = String(ctx.core?.selfInfo?.uin || '');
    const isSelf = selfUin && selfUin === String(wallet.senderUin);

    // 灰条是「已领取」提示，不再抢
    // 拼手气红包：自己发的也可抢（由 grabSelf 开关控制）
    const allowGrab = config.autoGrab && !opts.fromGrayTip && (!isSelf || config.grabSelf);
    if (allowGrab) {
      try {
        if (!wallet.pcBody) {
          ctx.logger?.warn?.('[红包监控] 缺少 pcBody，无法自动领取');
        } else {
          if (config.handlePassword) await sendPasswordIfNeeded(ctx, wallet);
          await sleep(randomDelay(config.delayMin, config.delayMax));
          const grab = await grabRedBag(ctx, wallet);
          const recAfter = getRecord(wallet.billNo)!;
          const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
          const grabTime = Math.floor(Date.now() / 1000);
          let claims = recAfter.claims || [];
          if (grab.amount > 0) {
            claims = mergeClaims(claims, [
              {
                uin: selfUin,
                name: selfNick,
                amount: grab.amount,
                time: grabTime,
                timeText: formatTime(grabTime),
              },
            ]);
          }
          upsertRecord({
            ...recAfter,
            grabbed: true,
            myAmount: grab.amount,
            claims,
            updatedAt: Date.now(),
          });
          ctx.logger?.info?.(
            `[红包监控] 领取完成 amount=${grab.amount}` + (isSelf ? ' (自己发的包)' : '')
          );
        }
      } catch (e) {
        ctx.logger?.error?.('[红包监控] 领取失败', e);
      }
    } else if (isSelf && !config.grabSelf) {
      ctx.logger?.info?.('[红包监控] 自己发的红包：grabSelf=关闭，跳过自动领取');
    }

    if (config.autoPullDetail && (wallet.pcBody || existing?.pcBody)) {
      try {
        await sleep(isSelf || opts.fromGrayTip ? 1500 : 1000);
        const detail = await pullDetail(ctx, {
          ...wallet,
          pcBody: wallet.pcBody || existing?.pcBody,
          stringIndex: wallet.stringIndex || existing?.stringIndex,
        });
        const rec = getRecord(wallet.billNo)!;
        const combined = combineClaimsFromDetail(rec.claims || [], detail.raw, undefined, {
          senderUin: rec.senderUin,
          senderName: rec.senderName,
        });
        upsertRecord({
          ...rec,
          claims: combined.claims,
          summary: combined.summary,
          rawDetail: detail.raw,
          updatedAt: Date.now(),
        });
        ctx.logger?.info?.(
          `[红包监控] 详情 已领 ${combined.summary?.recvNum ?? '?'}/${combined.summary?.totalNum ?? '?'} 人数=${combined.claims.length}`
        );
      } catch (e) {
        ctx.logger?.warn?.('[红包监控] pullDetail 失败（可稍后 /query refresh）', e);
      }
    }
  } finally {
    // 稍后再放开，避免短时间重复触发
    setTimeout(() => processingBills.delete(wallet.billNo), 8000);
  }
}
