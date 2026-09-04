import type { ClaimRecord, RedPacketSummary, WalletContext } from './types.js';
import fs from 'fs';
import path from 'path';

let pullDetailCaptureDir = '';
export function setPullDetailCaptureDir(dir: string) {
  pullDetailCaptureDir = dir || '';
}

export function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export function randomDelay(min: number, max: number) {
  const a = Math.max(0, min);
  const b = Math.max(a, max);
  return Math.floor(Math.random() * (b - a + 1)) + a;
}

export function formatTime(sec: number): string {
  if (!sec || sec <= 0) return '';
  return new Date(sec * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

/** QQ 号无效占位：协议里 luckyUin 常返回 "0" / null */
export function isValidUin(uin: unknown): boolean {
  const s = String(uin ?? '').trim();
  if (!s) return false;
  if (s === '0' || s === 'null' || s === 'undefined' || s === 'NaN') return false;
  return /^\d{5,}$/.test(s);
}

/** JSON 落盘时 Buffer 会变成 {0:8,1:2,...}，需还原为 grab/pull 可用的格式 */
export function normalizeBinaryField(v: any): string {
  if (v == null || v === '') return '';
  if (typeof v === 'string') {
    const s = v.trim();
    if (s.startsWith('0x') || s.startsWith('0X')) return s;
    if (/^[0-9a-fA-F]+$/.test(s)) return '0x' + s;
    return s;
  }
  if (typeof v === 'object') {
    const keys = Object.keys(v)
      .map(Number)
      .filter((n) => !Number.isNaN(n))
      .sort((a, b) => a - b);
    if (keys.length) {
      const hex = keys.map((k) => (Number(v[String(k)]) & 0xff).toString(16).padStart(2, '0')).join('');
      return hex ? '0x' + hex : '';
    }
  }
  return String(v);
}

export function normalizeStringIndex(v: any): string {
  if (v == null || v === '') return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    const keys = Object.keys(v)
      .map(Number)
      .filter((n) => !Number.isNaN(n))
      .sort((a, b) => a - b);
    if (keys.length) {
      return keys.map((k) => String.fromCharCode(Number(v[String(k)]) & 0xff)).join('');
    }
  }
  return String(v);
}

export function normalizeWalletFields(wallet: Partial<WalletContext>): Partial<WalletContext> {
  return {
    ...wallet,
    pcBody: normalizeBinaryField(wallet.pcBody),
    stringIndex: normalizeStringIndex(wallet.stringIndex),
    wishing: String(wallet.wishing || normalizeStringIndex(wallet.stringIndex) || ''),
  };
}

/** 从 NT 原始消息（RawMessage）提取红包 */
export function extractWalletFromRaw(raw: any, fallback?: any): WalletContext | null {
  if (!raw || typeof raw !== 'object') return null;

  // 优先：钱包元素 ElementType=9
  const fromWallet = extractFromWalletElements(raw, fallback);
  if (fromWallet) return fromWallet;

  // 兜底：红包领取灰条（busiId=19357），含 listid/pcbody/authkey
  const fromGray = extractFromGrayTip(raw, fallback);
  if (fromGray) return fromGray;

  return null;
}

function extractFromWalletElements(raw: any, fallback?: any): WalletContext | null {
  const elements = raw.elements;
  if (!Array.isArray(elements)) return null;

  for (const el of elements) {
    if (el?.elementType !== 9) continue;
    const w = el.walletElement;
    if (!w || typeof w !== 'object') {
      // walletElement 为空时，尝试从整个 element 里挖
      const bill = deepFindBillNo(el);
      if (!bill) continue;
      return buildWalletContext(raw, fallback, {
        billNo: bill,
        pcBody: deepFind(el, ['pcBody', 'pcbody', 'PCBody']),
        stringIndex: deepFind(el, ['stringIndex', 'index']),
        wishing: String(deepFind(el, ['authKey', 'authkey', 'wishing', 'title']) || ''),
        redChannel: Number(deepFind(el, ['redChannel']) || 0),
        walletElement: el,
      });
    }

    const billNo =
      w.billNo ||
      w.grabedMsg?.billNo ||
      w.redBag?.billNo ||
      w.receiver?.billNo ||
      w.listid ||
      w.listId ||
      deepFindBillNo(w) ||
      '';
    if (!billNo) continue;

    return buildWalletContext(raw, fallback, {
      billNo: String(billNo),
      pcBody: w.pcBody ?? w.pcbody,
      stringIndex: w.stringIndex ?? w.index,
      wishing: String(w.receiver?.title || w.redBag?.authKey || w.authKey || w.wishing || ''),
      redChannel: Number(w.redChannel ?? 0),
      walletElement: w,
    });
  }
  return null;
}

/** 解析红包相关灰条（领取提示），用于补录/查详情 */
export function extractFromGrayTip(raw: any, fallback?: any): WalletContext | null {
  const elements = raw?.elements;
  if (!Array.isArray(elements)) return null;

  for (const el of elements) {
    const g =
      el?.grayTipElement?.jsonGrayTipElement ||
      el?.jsonGrayTipElement ||
      el?.grayTipElement;
    if (!g) continue;

    const busiId = String(g.busiId || '');
    // 19357 = 领取了xx的红包
    const params = normalizeMap(g.xmlToJsonParam?.templParam || g.templParam);
    let listid = String(params.listid || params.listId || '');
    let pcbody = String(params.pcbody || params.pcBody || '');
    let authkey = String(params.authkey || params.authKey || '');
    let groupid = String(params.groupid || params.groupId || raw.peerUin || '');
    let packer = String(params.packer || '');

    // 从 jp 链接再解析一遍
    let jpListid = '';
    try {
      const items = JSON.parse(g.jsonStr || '{}')?.items || [];
      for (const it of items) {
        const jp = String(it?.jp || '');
        if (!jp.includes('listid=')) continue;
        const qs = jp.includes('?') ? jp.slice(jp.indexOf('?') + 1) : '';
        jpListid = new URLSearchParams(qs).get('listid') || jpListid;
      }
      const jp =
        items.find((x: any) => x?.jp)?.jp ||
        JSON.parse(g.jsonStr || '{}')?.items?.find((x: any) => x?.jp)?.jp ||
        '';
      if (jp) {
        const qs = jp.includes('?') ? jp.slice(jp.indexOf('?') + 1) : '';
        const sp = new URLSearchParams(qs);
        listid = listid || sp.get('listid') || jpListid || '';
        pcbody = pcbody || sp.get('pcbody') || '';
        authkey = authkey || sp.get('authkey') || '';
        groupid = groupid || sp.get('groupid') || '';
      }
    } catch {
      /* ignore */
    }
    listid = listid || jpListid;
    authkey = sanitizeAuthKey(authkey);

    // 非红包相关灰条且无 listid/pcbody 则跳过
    if (!listid && !pcbody) continue;
    if (busiId && !['19357', '19360', '12'].includes(busiId) && !listid) continue;
    // 没有 listid 就没法当 billNo
    if (!listid) continue;

    const graber = String(params.graber || params.grabber || params.uin || '');
    const jsonStr = String(g.jsonStr || '');
    const parsedTip = parseGrayTipJsonStr(jsonStr);
    let graberName = parsedTip.name;
    const members = g.xmlToJsonParam?.members || params.members;
    if (members && typeof members === 'object') {
      const memMap = members instanceof Map ? members : members;
      const memObj =
        memMap instanceof Map ? Object.fromEntries(memMap.entries()) : (memMap as Record<string, string>);
      if (!graberName && parsedTip.uid) graberName = String(memObj[parsedTip.uid] || '');
    }
    if (!graber && parsedTip.uin) {
      params.graber = parsedTip.uin;
    }

    // 灰条是「领取事件」，不是发包；sender 用 packer
    return buildWalletContext(raw, fallback, {
      billNo: listid,
      pcBody: pcbody,
      stringIndex: authkey,
      wishing: authkey,
      redChannel: 0,
      walletElement: {
        fromGrayTip: true,
        busiId,
        params,
        graber: graber || parsedTip.uin,
        graberName,
        jsonStr,
      },
      peerUinOverride: groupid || undefined,
      senderUinOverride: packer || undefined,
    });
  }
  return null;
}

function normalizeMap(v: any): Record<string, string> {
  if (!v) return {};
  if (v instanceof Map) {
    const o: Record<string, string> = {};
    for (const [k, val] of v.entries()) o[String(k)] = String(val);
    return o;
  }
  if (typeof v === 'object') {
    const o: Record<string, string> = {};
    for (const [k, val] of Object.entries(v)) o[String(k)] = String(val as any);
    return o;
  }
  return {};
}

/** 解析灰条 jsonStr 中的领取人昵称/QQ/金额（第一个 type=qq 为领取人） */
export function parseGrayTipJsonStr(jsonStr: string): {
  name: string;
  uin: string;
  uid: string;
  amount: number;
} {
  let name = '';
  let uin = '';
  let uid = '';
  let amount = 0;
  if (!jsonStr) return { name, uin, uid, amount };
  try {
    const items = JSON.parse(jsonStr)?.items || [];
    let graberDone = false;
    for (const it of items) {
      if (it?.type === 'qq' && !graberDone) {
        graberDone = true;
        if (it.uid) uid = String(it.uid).trim();
        if (it.uin) uin = String(it.uin).trim();
        if (it.txt) name = String(it.txt).trim();
        continue;
      }
      if (it?.type === 'text' && it?.txt) {
        const t = String(it.txt);
        const m1 = t.match(/^(.+?)领取了/);
        if (m1 && !name) name = m1[1].trim();
        const m2 = t.match(/([\d.]+)\s*元/);
        if (m2) amount = parseFloat(m2[1]) || 0;
      }
    }
  } catch {
    /* ignore */
  }
  return { name, uin, uid, amount };
}

function deepFindBillNo(obj: any): string {
  const hit = deepFind(obj, ['billNo', 'bill_no', 'listid', 'listId', 'ListId']);
  return hit != null ? String(hit) : '';
}

function deepFind(obj: any, keys: string[], depth = 0): any {
  if (!obj || typeof obj !== 'object' || depth > 5) return undefined;
  for (const k of keys) {
    if (obj[k] != null && obj[k] !== '') return obj[k];
  }
  for (const v of Object.values(obj)) {
    if (v && typeof v === 'object') {
      const r = deepFind(v, keys, depth + 1);
      if (r != null && r !== '') return r;
    }
  }
  return undefined;
}

function buildWalletContext(
  raw: any,
  fallback: any,
  extra: {
    billNo: string;
    pcBody: any;
    stringIndex: any;
    wishing: string;
    redChannel: number;
    walletElement: any;
    peerUinOverride?: string;
    senderUinOverride?: string;
  }
): WalletContext {
  const chatType = Number(raw.chatType ?? (fallback?.message_type === 'group' ? 2 : 1));
  const peerUin = String(
    extra.peerUinOverride || raw.peerUin || fallback?.group_id || fallback?.user_id || ''
  );
  const msgTime = Number(raw.msgTime || fallback?.time || Math.floor(Date.now() / 1000));
  return {
    walletElement: extra.walletElement,
    billNo: String(extra.billNo),
    peerUid: String(raw.peerUid || peerUin),
    peerUin,
    senderUin: String(
      extra.senderUinOverride || raw.senderUin || fallback?.user_id || ''
    ),
    senderName: String(
      raw.sendMemberName || raw.sendNickName || fallback?.sender?.nickname || ''
    ),
    peerName: String(raw.peerName || fallback?.group_name || peerUin || ''),
    chatType,
    msgSeq: String(raw.msgSeq || ''),
    msgTime,
    wishing: extra.wishing,
    pcBody: extra.pcBody,
    stringIndex: extra.stringIndex,
    redChannel: extra.redChannel,
  };
}

/** 诊断：消息里是否有红包相关痕迹 */
export function diagnoseRedPacketRaw(raw: any): string | null {
  if (!raw?.elements || !Array.isArray(raw.elements)) return null;
  const types = raw.elements.map((e: any) => e?.elementType);
  if (types.includes(9)) {
    const el = raw.elements.find((e: any) => e?.elementType === 9);
    const w = el?.walletElement;
    const keys = w && typeof w === 'object' ? Object.keys(w).slice(0, 20).join(',') : String(w);
    return `ElementType=9 walletKeys=[${keys}]`;
  }
  for (const el of raw.elements) {
    const g = el?.grayTipElement?.jsonGrayTipElement || el?.jsonGrayTipElement;
    if (g && String(g.busiId) === '19357') return 'GrayTip busiId=19357 (红包领取提示)';
  }
  return null;
}

/** 从 OneBot 消息事件中提取红包（walletElement） */
export function extractWallet(event: any): WalletContext | null {
  if (!event) return null;

  // 1) debug 模式下 NapCat 会挂 raw
  if (event.raw) {
    const fromRaw = extractWalletFromRaw(event.raw, event);
    if (fromRaw) return fromRaw;
  }

  // 2) 事件自身或嵌套对象上带 elements
  const candidates: any[] = [event];
  for (const k of Object.keys(event || {})) {
    const v = event[k];
    if (v && typeof v === 'object' && Array.isArray(v.elements)) candidates.push(v);
  }
  for (const raw of candidates) {
    const hit = extractWalletFromRaw(raw, event);
    if (hit) return hit;
  }
  return null;
}

export function getMsgService(ctx: any) {
  return ctx?.core?.context?.session?.getMsgService?.();
}

/**
 * 通过 NapCat eventWrapper 永久监听内核消息
 * （比自行 addKernelMsgListener 更稳，与 OneBot 共用同一分发器）
 */
export function attachKernelMsgListener(
  ctx: any,
  onRawMsg: (raw: any) => void | Promise<void>
): (() => void) | null {
  try {
    const ew = ctx?.core?.eventWrapper;
    if (!ew?.createListenerFunction || !ew?.EventTask) {
      ctx?.logger?.warn?.('[红包监控] eventWrapper 不可用，尝试直挂 MsgService');
      return attachViaMsgService(ctx, onRawMsg);
    }

    const main = 'NodeIKernelMsgListener';
    ew.createListenerFunction(main);
    if (!ew.EventTask.get(main)) ew.EventTask.set(main, new Map());
    const subMap: Map<string, Map<string, any>> = ew.EventTask.get(main);

    const ids: string[] = [];
    const bind = (sub: string, wrap: (args: any[]) => void) => {
      if (!subMap.get(sub)) subMap.set(sub, new Map());
      const id = `hb_${sub}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      subMap.get(sub)!.set(id, {
        timeout: 86400e3 * 365,
        createtime: Date.now(),
        checker: () => true,
        func: (...args: any[]) => {
          try {
            wrap(args);
          } catch (e) {
            ctx?.logger?.error?.(`[红包监控] ${sub} 处理异常`, e);
          }
        },
      });
      ids.push(sub + '/' + id);
    };

    const feed = (msg: any) => {
      if (!msg) return;
      Promise.resolve(onRawMsg(msg)).catch((e) =>
        ctx?.logger?.error?.('[红包监控] 内核消息处理失败', e)
      );
    };

    bind('onRecvMsg', (args) => {
      const msgs = args[0];
      if (Array.isArray(msgs)) msgs.forEach(feed);
      else feed(msgs);
    });
    bind('onAddSendMsg', (args) => {
      const msg = args[0];
      // 发送中也尝试解析（有的版本 wallet 已完整）；成功态再解析一次
      feed(msg);
    });
    bind('onMsgInfoListUpdate', (args) => {
      const msgs = args[0];
      const list = Array.isArray(msgs) ? msgs : [msgs];
      for (const m of list) {
        if (!m?.elements) continue;
        const hit = m.elements.some(
          (el: any) =>
            el?.elementType === 9 ||
            el?.grayTipElement?.jsonGrayTipElement?.busiId === '19357' ||
            el?.jsonGrayTipElement?.busiId === '19357'
        );
        if (hit) feed(m);
      }
    });

    ctx?.logger?.info?.(
      '[红包监控] 已挂接内核消息监听 (eventWrapper onRecvMsg/onAddSendMsg/onMsgInfoListUpdate)'
    );

    return () => {
      for (const key of ids) {
        const [sub, id] = key.split('/');
        subMap.get(sub)?.delete(id);
      }
    };
  } catch (e) {
    ctx?.logger?.error?.('[红包监控] 挂接内核监听失败', e);
    return attachViaMsgService(ctx, onRawMsg);
  }
}

/** 从群聊天记录扫描指定红包的领取灰条（补录历史领取人） */
export async function scanGrayTipsFromHistory(
  ctx: any,
  rec: { billNo: string; groupId: string; msgSeq?: string; msgTime?: number; peerUid?: string }
): Promise<ClaimRecord[]> {
  const msgApi = ctx?.core?.apis?.MsgApi;
  if (!msgApi || !rec.billNo || !rec.groupId) return [];

  const peer = { chatType: 2, peerUid: String(rec.groupId), guildId: '' };
  const msgTime = Number(rec.msgTime || 0);
  const lists: any[][] = [];

  const pushList = (rsp: any) => {
    const arr = rsp?.msgList || rsp?.messages || rsp?.list;
    if (Array.isArray(arr) && arr.length) lists.push(arr);
  };

  try {
    if (rec.msgSeq && msgApi.getMsgsBySeqAndCount) {
      for (const [order, count] of [
        [true, 120],
        [false, 120],
      ] as const) {
        try {
          pushList(
            await msgApi.getMsgsBySeqAndCount(peer, String(rec.msgSeq), count, order, true)
          );
        } catch {
          /* try next */
        }
      }
    }
    if (msgApi.getMsgsBySeqList && rec.msgSeq) {
      try {
        const seq = parseInt(String(rec.msgSeq), 10);
        const seqList = Array.from({ length: 80 }, (_, i) => String(seq + i));
        pushList(await msgApi.getMsgsBySeqList(peer, seqList));
      } catch {
        /* ignore */
      }
    }
    if (msgTime > 0 && msgApi.queryMsgsWithFilterEx) {
      try {
        pushList(
          await msgApi.queryMsgsWithFilterEx('0', String(msgTime), String(rec.msgSeq || '0'), {
            chatInfo: peer,
            filterMsgType: [{ type: 5, subType: [] }],
            filterSendersUid: [],
            filterMsgFromTime: String(msgTime - 5),
            filterMsgToTime: '0',
            pageLimit: 300,
            isReverseOrder: false,
            isIncludeCurrent: true,
          })
        );
      } catch {
        /* ignore */
      }
    }
    if (rec.msgSeq && msgApi.getMsgsBySeqAndCount) {
      try {
        // false = 从该 seq 往更新方向取（领取灰条在红包消息之后）
        pushList(await msgApi.getMsgsBySeqAndCount(peer, String(rec.msgSeq), 100, false, true));
      } catch {
        /* ignore */
      }
    }
    if (msgApi.getMsgHistory && rec.msgSeq) {
      for (const [reverse, count] of [
        [false, 400],
        [true, 400],
      ] as const) {
        try {
          pushList(await msgApi.getMsgHistory(peer, String(rec.msgSeq), count, reverse));
        } catch {
          /* ignore */
        }
      }
    }
    if (msgApi.getAioFirstViewLatestMsgs) {
      try {
        pushList(await msgApi.getAioFirstViewLatestMsgs(peer, 500));
      } catch {
        /* ignore */
      }
    }
  } catch (e) {
    ctx?.logger?.warn?.('[红包监控] 扫描群历史失败', e);
    return [];
  }

  const seenMsg = new Set<string>();
  const out: ClaimRecord[] = [];
  for (const msgList of lists) {
    for (const raw of msgList) {
      const mid = String(raw?.msgId || raw?.msgSeq || '');
      if (mid && seenMsg.has(mid)) continue;
      if (mid) seenMsg.add(mid);
      const t = Number(raw?.msgTime || 0);
      if (msgTime > 0 && t > 0 && t < msgTime - 2) continue;

      const wallet = extractWalletFromRaw(raw);
      if (!wallet?.walletElement?.fromGrayTip) continue;
      if (wallet.billNo !== rec.billNo) continue;
      const claim = extractGrayTipClaim(wallet);
      if (claim) out.push(claim);
    }
  }
  return mergeClaims([], out);
}

/** 监听内核 onGrabPasswordRedBag，捕获他人领取金额 */
export function attachGrabRedBagListener(
  ctx: any,
  onClaim: (info: { billNo: string; uin: string; name: string; amount: number; time: number }) => void
): (() => void) | null {
  try {
    const ew = ctx?.core?.eventWrapper;
    if (!ew?.createListenerFunction || !ew?.EventTask) return null;

    const main = 'NodeIKernelMsgListener';
    ew.createListenerFunction(main);
    if (!ew.EventTask.get(main)) ew.EventTask.set(main, new Map());
    const subMap: Map<string, Map<string, any>> = ew.EventTask.get(main);

    const id = `hb_grab_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    if (!subMap.get('onGrabPasswordRedBag')) subMap.set('onGrabPasswordRedBag', new Map());
    subMap.get('onGrabPasswordRedBag')!.set(id, {
      timeout: 86400e3 * 365,
      createtime: Date.now(),
      checker: () => true,
      func: (...args: any[]) => {
        try {
          const info = parseGrabPasswordRedBagArgs(args);
          if (info) onClaim(info);
        } catch (e) {
          ctx?.logger?.warn?.('[红包监控] onGrabPasswordRedBag 解析失败', e);
        }
      },
    });

    ctx?.logger?.info?.('[红包监控] 已挂接 onGrabPasswordRedBag 监听');
    return () => subMap.get('onGrabPasswordRedBag')?.delete(id);
  } catch (e) {
    ctx?.logger?.warn?.('[红包监控] 挂接 onGrabPasswordRedBag 失败', e);
    return null;
  }
}

function parseGrabPasswordRedBagArgs(args: any[]): {
  billNo: string;
  uin: string;
  name: string;
  amount: number;
  time: number;
} | null {
  const flat = args.flatMap((a) => (a && typeof a === 'object' ? [a, ...Object.values(a)] : [a]));
  let billNo = '';
  let uin = '';
  let name = '';
  let amount = 0;
  let time = Math.floor(Date.now() / 1000);

  const walk = (v: any, depth = 0) => {
    if (!v || typeof v !== 'object' || depth > 4) return;
    for (const [k, val] of Object.entries(v)) {
      const key = String(k).toLowerCase();
      if (typeof val === 'string' || typeof val === 'number') {
        const s = String(val);
        if (!billNo && (key.includes('listid') || key.includes('billno') || key === 'bill_no'))
          billNo = s;
        if (!uin && (key.includes('recvuin') || key === 'uin' || key === 'qq'))
          uin = s;
        if (!name && (key.includes('recvname') || key === 'name' || key === 'nick'))
          name = s;
        if (!amount && (key.includes('amount') || key === 'money')) {
          const n = parseInt(s, 10);
          if (n > 0) amount = n > 100 ? n / 100 : n;
        }
        if (key.includes('time') && /^\d+$/.test(s)) {
          const n = Number(s);
          time = n > 1e12 ? Math.floor(n / 1000) : n;
        }
      } else if (val && typeof val === 'object') {
        walk(val, depth + 1);
      }
    }
  };
  for (const a of args) walk(a);

  if (!billNo || (!uin && !name)) return null;
  return { billNo, uin, name, amount, time };
}

function attachViaMsgService(ctx: any, onRawMsg: (raw: any) => void | Promise<void>) {
  try {
    const msgService = getMsgService(ctx);
    if (!msgService?.addKernelMsgListener) return null;
    const handler = {
      onRecvMsg(msgs: any[]) {
        if (!Array.isArray(msgs)) return;
        for (const m of msgs) Promise.resolve(onRawMsg(m)).catch(() => {});
      },
      onAddSendMsg(msg: any) {
        if (msg) Promise.resolve(onRawMsg(msg)).catch(() => {});
      },
      onMsgInfoListUpdate(msgs: any[]) {
        if (!Array.isArray(msgs)) return;
        for (const m of msgs) Promise.resolve(onRawMsg(m)).catch(() => {});
      },
    };
    const proxy = new Proxy(handler, {
      get(target, prop) {
        const v = (target as any)[prop];
        return typeof v === 'function' ? v.bind(target) : () => {};
      },
    });
    msgService.addKernelMsgListener(proxy);
    ctx?.logger?.info?.('[红包监控] 已直挂 MsgService 监听');
    return () => {
      try {
        msgService.removeKernelMsgListener?.(proxy);
      } catch {
        /* ignore */
      }
    };
  } catch {
    return null;
  }
}

/** 领取红包 —— 调用 QQ NT 内核 grabRedBag（非模拟点击） */
export async function grabRedBag(ctx: any, wallet: WalletContext) {
  wallet = normalizeWalletFields(wallet) as WalletContext;
  const msgService = getMsgService(ctx);
  if (!msgService?.grabRedBag) {
    throw new Error('grabRedBag API 不可用，请确认 NapCat/QQNT 已登录');
  }

  const selfInfo = ctx.core?.selfInfo;
  const selfUin = String(selfInfo?.uin || '');
  const selfNick = selfInfo?.nick || selfUin;
  const recvUin = wallet.chatType === 1 ? selfUin : wallet.peerUin;

  // 已验证：平铺参数 + 原始 pcBody/stringIndex 才能成功
  const params = {
    recvUin,
    recvType: wallet.chatType,
    peerUid: wallet.peerUid,
    name: selfNick,
    pcBody: wallet.pcBody,
    wishing: wallet.wishing,
    msgSeq: wallet.msgSeq,
    index: wallet.stringIndex,
  };
  const authKey = sanitizeAuthKey((wallet as WalletContext).authKey || '');
  if (authKey) {
    Object.assign(params, {
      authkey: authKey,
      authKey,
      listid: wallet.billNo,
      listId: wallet.billNo,
      groupid: wallet.peerUin || wallet.peerUid,
      grouptype: 1,
    });
  }

  const ret = await Promise.race([
    msgService.grabRedBag(params),
    sleep(8000).then(() => ({ __timeout: true })),
  ]);

  if ((ret as any)?.__timeout) {
    return { ok: true, timeout: true, amount: 0, raw: null };
  }

  const rsp = (ret as any)?.grabRedBagRsp || ret;
  const amountFen = parseInt(String(rsp?.recvdOrder?.amount ?? '0'), 10) || 0;
  return {
    ok: true,
    timeout: false,
    amount: amountFen / 100,
    raw: ret,
  };
}

/** 从灰条 jp / params 提取 authkey（仅保留 32 位 hex） */
function sanitizeAuthKey(v: unknown): string {
  const s = String(v || '').trim();
  const m = s.match(/[0-9a-fA-F]{32}/);
  return m ? m[0].toLowerCase() : '';
}

function parseAuthKeyFromGrayTipElement(g: any): string {
  const params = normalizeMap(g?.xmlToJsonParam?.templParam || g?.templParam);
  let authkey = sanitizeAuthKey(params.authkey || params.authKey);
  try {
    const items = JSON.parse(g?.jsonStr || '{}')?.items || [];
    for (const it of items) {
      const jp = String(it?.jp || '');
      if (!jp.includes('authkey=')) continue;
      const qs = jp.includes('?') ? jp.slice(jp.indexOf('?') + 1) : '';
      authkey = authkey || sanitizeAuthKey(new URLSearchParams(qs).get('authkey'));
    }
  } catch {
    /* ignore */
  }
  return authkey;
}

/** 扫描群历史，合并红包本体 + 灰条里的 pcBody/stringIndex/authKey */
export async function scanWalletMetaFromHistory(
  ctx: any,
  rec: { billNo: string; groupId?: string; peerUid?: string; msgSeq?: string; msgTime?: number }
): Promise<{ pcBody?: string; stringIndex?: string; authKey?: string; wishing?: string }> {
  const msgApi = ctx?.core?.apis?.MsgApi;
  const groupId = String(rec.groupId || rec.peerUid || '');
  const billNo = rec.billNo;
  if (!msgApi || !groupId || !billNo) return {};

  const peer = { chatType: 2, peerUid: groupId, guildId: '' };
  const msgs: any[] = [];
  const push = (rsp: any) => {
    const arr = rsp?.msgList || rsp?.messages || rsp?.list;
    if (Array.isArray(arr)) msgs.push(...arr);
  };
  try {
    if (rec.msgSeq && msgApi.getMsgsBySeqAndCount) {
      for (const [order, count] of [
        [true, 40],
        [false, 40],
      ] as const) {
        try {
          push(await msgApi.getMsgsBySeqAndCount(peer, String(rec.msgSeq), count, order, true));
        } catch {
          /* ignore */
        }
      }
    }
    if (msgApi.getAioFirstViewLatestMsgs) {
      try {
        push(await msgApi.getAioFirstViewLatestMsgs({ peerUid: groupId, chatType: 2 }));
      } catch {
        /* ignore */
      }
    }
  } catch {
    return {};
  }

  let pcBody = '';
  let stringIndex = '';
  let authKey = '';
  let wishing = '';
  const seen = new Set<string>();

  for (const raw of msgs) {
    const mid = String(raw?.msgId || raw?.msgSeq || '');
    if (mid && seen.has(mid)) continue;
    if (mid) seen.add(mid);

    for (const el of raw?.elements || []) {
      if (el?.elementType === 9 && el?.walletElement) {
        const w = el.walletElement;
        const bn = String(w.billNo || w.listid || w.listId || '');
        if (bn !== billNo) continue;
        pcBody = pcBody || normalizeBinaryField(w.pcBody ?? w.pcbody);
        stringIndex = stringIndex || normalizeStringIndex(w.stringIndex ?? w.index);
        wishing =
          wishing ||
          String(w.receiver?.title || w.wishing || '');
        // 红包本体上的 authKey 字段常不是 jp 里的 authkey，勿抢先写入
      }
      const g = el?.grayTipElement?.jsonGrayTipElement || el?.jsonGrayTipElement;
      if (!g || String(g.busiId || '') !== '19357') continue;
      const params = normalizeMap(g.xmlToJsonParam?.templParam || g.templParam);
      const listid = String(params.listid || params.listId || '');
      if (listid !== billNo) continue;
      pcBody = pcBody || normalizeBinaryField(params.pcbody || params.pcBody);
      // 灰条 jp 的 authkey 才是详情页同源密钥
      authKey = authKey || parseAuthKeyFromGrayTipElement(g);
    }
  }
  return { pcBody, stringIndex, authKey: sanitizeAuthKey(authKey), wishing };
}

/** 从群消息重新取 pcBody/authKey（与 PC 点红包详情同源） */
export async function refreshWalletFromMessage(
  ctx: any,
  wallet: Partial<WalletContext> & { billNo: string }
): Promise<Partial<WalletContext> & { billNo: string }> {
  const groupId = String(wallet.groupId || wallet.peerUin || wallet.peerUid || '');
  if (!groupId) return wallet;
  // 已有完整领取参数时直接复用：跳过历史消息扫描（省 5~8 秒，领取回复更快）
  if (wallet.pcBody && wallet.stringIndex) return wallet;

  const meta = await scanWalletMetaFromHistory(ctx, {
    billNo: wallet.billNo,
    groupId,
    peerUid: groupId,
    msgSeq: wallet.msgSeq,
    msgTime: wallet.msgTime,
  });

  return {
    ...wallet,
    pcBody: meta.pcBody || wallet.pcBody,
    stringIndex: meta.stringIndex || wallet.stringIndex,
    authKey: meta.authKey || (wallet as WalletContext).authKey,
    wishing: meta.wishing || wallet.wishing,
  };
}

/** 从 pullDetail 响应中提取领取列表（兼容多种字段名） */
export function extractRecvListFromRaw(raw: any): any[] {
  const rsp = raw?.pullDetailRsp || raw?.detail || raw;
  if (!rsp || typeof rsp !== 'object') return [];
  const lists: any[] = [];
  for (const key of [
    'recvdOrderList',
    'recvList',
    'recvdList',
    'recv_details',
    'recvDetails',
    'detailList',
    'orderList',
    'grabedList',
    'list',
  ]) {
    const c = rsp[key];
    if (Array.isArray(c) && c.length) lists.push(...c);
  }
  if (!lists.length && rsp.recvdOrder) lists.push(rsp.recvdOrder);
  return lists;
}

/** 挂接 pullDetail 调试：落盘参数，便于对比 PC 点开详情时的真实请求 */
export function hookPullDetailForDebug(ctx: any) {
  try {
    const msgService = getMsgService(ctx);
    if (!msgService?.pullDetail) return;
    const current = msgService.pullDetail;
    const orig =
      (msgService as any).__hbPullDetailOrig ||
      ((msgService as any).__hbPullDetailHooked ? null : current.bind(msgService));
    if (!orig) {
      // 已挂过但拿不到原函数：仍用当前函数（可能已是包装）
      (msgService as any).__hbPullDetailOrig = current.bind(msgService);
    }
    const real = ((msgService as any).__hbPullDetailOrig || current).bind(msgService);
    if (!(msgService as any).__hbPullDetailOrig) {
      (msgService as any).__hbPullDetailOrig = real;
    }
    const wrapped = async (arg: unknown) => {
      const ret = await (msgService as any).__hbPullDetailOrig(arg);
      try {
        const listLen = extractRecvListFromRaw(ret).length;
        const so = (ret as any)?.pullDetailRsp?.sendOrder || (ret as any)?.sendOrder;
        const safeArg = (() => {
          try {
            return JSON.parse(
              JSON.stringify(arg, (_k, v) => {
                if (typeof Buffer !== 'undefined' && Buffer.isBuffer?.(v)) {
                  return '0x' + v.toString('hex');
                }
                if (v && typeof v === 'object' && v.type === 'Buffer' && Array.isArray(v.data)) {
                  return '0x' + Buffer.from(v.data).toString('hex');
                }
                return v;
              })
            );
          } catch {
            return { stringifyError: true };
          }
        })();
        const rsp = (ret as any)?.pullDetailRsp || ret;
        const topKeys =
          rsp && typeof rsp === 'object' ? Object.keys(rsp).slice(0, 40) : [];
        const argKeys =
          arg && typeof arg === 'object'
            ? Object.keys(arg as object).concat(
                (arg as any)?.pullDetailReq
                  ? Object.keys((arg as any).pullDetailReq)
                  : []
              )
            : [];
        const entry = {
          at: Date.now(),
          recvListLen: listLen,
          recvNum: so?.recvNum,
          argKeys,
          topKeys,
          // 名单非空时额外留一份，便于对照 QQ 官方详情
          sampleRecv: listLen
            ? extractRecvListFromRaw(ret).slice(0, 8)
            : undefined,
          arg: safeArg,
        };
        ctx?.logger?.info?.(
          `[红包监控] pullDetail 钩子 recvListLen=${listLen} recvNum=${so?.recvNum}`
        );
        const dir = pullDetailCaptureDir || String(ctx?.dataPath || '');
        if (dir) {
          const file = path.join(dir, 'pullDetail_captures.json');
          let arr: unknown[] = [];
          try {
            if (fs.existsSync(file)) arr = JSON.parse(fs.readFileSync(file, 'utf-8'));
          } catch {
            arr = [];
          }
          if (!Array.isArray(arr)) arr = [];
          arr.push(entry);
          if (arr.length > 40) arr = arr.slice(-40);
          fs.writeFileSync(file, JSON.stringify(arr, null, 2), 'utf-8');
        }
      } catch (e) {
        ctx?.logger?.warn?.('[红包监控] pullDetail 落盘失败', e);
      }
      return ret;
    };
    try {
      (msgService as any).pullDetail = wrapped;
    } catch {
      Object.defineProperty(msgService, 'pullDetail', { value: wrapped, writable: true, configurable: true });
    }
    (msgService as any).__hbPullDetailHooked = true;
    ctx?.logger?.info?.('[红包监控] 已挂接 pullDetail 调试钩子（落盘）');
  } catch (e) {
    ctx?.logger?.warn?.('[红包监控] pullDetail 调试钩子跳过（内核只读）', e);
  }
}

async function invokePullDetail(msgService: any, payload: unknown): Promise<any | null> {
  try {
    const ret = await Promise.race([msgService.pullDetail(payload), sleep(8000).then(() => null)]);
    try {
      const dir = pullDetailCaptureDir;
      if (dir && ret) {
        const listLen = extractRecvListFromRaw(ret).length;
        const so = (ret as any)?.pullDetailRsp?.sendOrder || (ret as any)?.sendOrder;
        const file = path.join(dir, 'pullDetail_captures.json');
        let arr: unknown[] = [];
        try {
          if (fs.existsSync(file)) arr = JSON.parse(fs.readFileSync(file, 'utf-8'));
        } catch {
          arr = [];
        }
        if (!Array.isArray(arr)) arr = [];
        arr.push({
          at: Date.now(),
          via: 'invoke',
          recvListLen: listLen,
          recvNum: so?.recvNum,
          arg: JSON.parse(
            JSON.stringify(payload, (_k, v) =>
              typeof Buffer !== 'undefined' && Buffer.isBuffer?.(v) ? '0x' + v.toString('hex') : v
            )
          ),
        });
        if (arr.length > 60) arr = arr.slice(-60);
        fs.writeFileSync(file, JSON.stringify(arr, null, 2), 'utf-8');
      }
    } catch {
      /* ignore capture errors */
    }
    return ret;
  } catch {
    return null;
  }
}

/** 汇总 + 分页拉取领取名单（PC 点详情走 queryType/offset） */
async function pullDetailRecvList(
  msgService: any,
  base: Record<string, unknown>,
  recvNum: number,
  selfUin?: string
): Promise<{ claims: ClaimRecord[]; listRaw: any | null }> {
  let merged: ClaimRecord[] = [];
  let listRaw: any = null;

  // PC 点「领取详情」常为连续 flat pullDetail：先汇总，再带内核同名字段分页
  await invokePullDetail(msgService, base);
  const authKey = sanitizeAuthKey(base.authkey || base.authKey || base.index || '');
  const listId = String(base.listid || base.listId || base.billNo || '');
  const groupId = String(base.groupid || base.groupId || base.peerUid || '');
  for (const queryType of [1, 2, 0]) {
    for (const count of [recvNum || 20, 20, 150]) {
      if (!count) continue;
      const nativePage = {
        ...base,
        queryType,
        offset: 0,
        count,
        limit: count,
        int32_offset: 0,
        int32_limit: count,
        uint64_groupid: groupId,
        bytes_pc_authkey: authKey,
        bytes_pc_listid: listId,
        uint32_grouptype: 1,
      };
      const ret = await invokePullDetail(msgService, nativePage);
      if (!ret || !isValidPullDetailRaw(ret)) continue;
      const list = extractRecvListFromRaw(ret);
      if (!list.length) continue;
      if (!listRaw) listRaw = ret;
      merged = mergeClaims(merged, normalizeClaims(ret));
      if (recvNum > 0 && merged.length >= recvNum) return { claims: merged, listRaw };
    }
  }

  const recvUinCandidates = [base.recvUin, selfUin, ...(base.recvType === 2 ? [base.peerUid] : [])].filter(
    Boolean
  );
  const countCandidates = [150, 100, 20];
  const queryTypes = [1, 2, 0];

  for (const recvUin of recvUinCandidates) {
    for (const queryType of queryTypes) {
      for (const count of countCandidates) {
        let offset = 0;
        for (let page = 0; page < 5; page++) {
          const listBase = { ...base, recvUin, queryType, offset, count };
          const payloads = [
            { pullDetailReq: listBase },
            listBase,
            { pullDetailReq: { ...listBase, queryRecvList: 1 } },
            { ...listBase, queryRecvList: 1, needRecvList: true },
            { pullDetailReq: { ...listBase, query_type: queryType, recvOffset: offset, recvCount: count } },
            { pullDetailReq: { ...listBase, pageIndex: offset, pageSize: count } },
            { pullDetailReq: { ...base, recvUin, queryType: String(queryType), offset: String(offset), count: String(count) } },
          ];
          let pageAdded = 0;
          for (const payload of payloads) {
            const ret = await invokePullDetail(msgService, payload);
            if (!ret || !isValidPullDetailRaw(ret)) continue;
            const list = extractRecvListFromRaw(ret);
            if (!list.length) continue;
            if (!listRaw) listRaw = ret;
            const before = merged.length;
            merged = mergeClaims(merged, normalizeClaims(ret));
            pageAdded = Math.max(pageAdded, merged.length - before);
          }
          if (recvNum > 0 && merged.length >= recvNum) {
            return { claims: merged, listRaw };
          }
          if (pageAdded <= 0) break;
          offset += pageAdded;
        }
        if (merged.length >= recvNum && recvNum > 0) return { claims: merged, listRaw };
      }
    }
  }
  return { claims: merged, listRaw };
}

/** pullDetail 返回是否有效（避免错误尝试污染缓存） */
export function isValidPullDetailRaw(raw: any): boolean {
  if (!raw || typeof raw !== 'object') return false;
  const code = raw.result;
  if (code !== 0 && code !== '0') return false;
  if (String(raw.errMsg || '').toLowerCase().includes('error')) return false;
  const so = raw?.pullDetailRsp?.sendOrder || raw?.sendOrder;
  if (!so) return false;
  const totalNum = parseInt(String(so.totalNum ?? '0'), 10) || 0;
  const recvNum = parseInt(String(so.recvNum ?? '0'), 10) || 0;
  return totalNum > 0 || recvNum > 0;
}

/** pullDetail 响应评分：优先有领取名单，其次 recvNum */
export function scorePullDetailRaw(raw: any): number {
  if (!isValidPullDetailRaw(raw)) return -1;
  const listLen = extractRecvListFromRaw(raw).length;
  const so = raw?.pullDetailRsp?.sendOrder || raw?.sendOrder;
  const recvNum = parseInt(String(so?.recvNum ?? '0'), 10) || 0;
  return listLen * 1000 + recvNum;
}

/** 构造 pullDetail 请求体（含灰条 jp 里的 authkey/groupid/listid；带上 sendOrder 同源 channel/busType） */
function buildPullDetailBase(
  wallet: Partial<WalletContext> & { billNo: string; pcBody?: string; redChannel?: number },
  ctx: {
    recvUin: string;
    selfNick: string;
    chatType: number;
    peerUid: string;
    groupId: string;
    authKey: string;
    index: unknown;
  }
): Record<string, unknown> {
  const channel = Number(wallet.redChannel || 1) || 1;
  const busType = 2; // 群拼手气常见值（与 pullDetailRsp.sendOrder.busType 一致）
  const auth = sanitizeAuthKey(ctx.authKey);
  const jp = auth
    ? {
        authkey: auth,
        authKey: auth,
        groupid: ctx.groupId,
        groupId: ctx.groupId,
        grouptype: 1,
        listId: wallet.billNo,
        listid: wallet.billNo,
      }
    : { listId: wallet.billNo, listid: wallet.billNo };
  return {
    recvUin: ctx.recvUin,
    recvType: ctx.chatType,
    peerUid: ctx.peerUid,
    name: ctx.selfNick,
    pcBody: wallet.pcBody,
    wishing: wallet.wishing || '',
    msgSeq: wallet.msgSeq || '',
    index: ctx.index,
    billNo: wallet.billNo,
    channel,
    busType,
    bus_type: busType,
    // 内核 RedBagWorker 日志同名字段：带 offset/limit + pc_authkey 才会返回 recvdOrderList
    uint64_groupid: ctx.groupId,
    int32_offset: 0,
    int32_limit: 150,
    offset: 0,
    limit: 150,
    count: 150,
    bytes_pc_authkey: auth || undefined,
    bytes_pc_listid: wallet.billNo,
    uint32_grouptype: 1,
    uint32_channel: channel,
    uint32_bus_type: busType,
    ...jp,
  };
}

/** 穷举 pullDetail 参数（调试 / 内部选优） */
export async function exhaustPullDetailAttempts(
  ctx: any,
  wallet: Partial<WalletContext> & { billNo: string },
  limit = 40
): Promise<Array<{ label: string; score: number; recvListLen: number; recvNum: string; error?: string }>> {
  wallet = normalizeWalletFields(wallet);
  wallet = await refreshWalletFromMessage(ctx, wallet);
  const msgService = getMsgService(ctx);
  if (!msgService?.pullDetail || !wallet.pcBody) return [];

  const selfUin = String(ctx.core?.selfInfo?.uin || '');
  const selfNick = String(ctx.core?.selfInfo?.nick || selfUin);
  const chatType = Number(wallet.chatType ?? 2);
  const peerUin = String(wallet.peerUin || wallet.peerUid || '');
  const peerUid = String(wallet.peerUid || peerUin);
  const senderUin = String((wallet as any).senderUin || '');
  const authKey = sanitizeAuthKey((wallet as WalletContext).authKey || '');
  const groupId = peerUin;
  const indexOrder = [...new Set([authKey, wallet.stringIndex].filter(Boolean))];
  const recvUinOrder =
    chatType === 2
      ? [...new Set([selfUin, peerUin, senderUin].filter(Boolean))]
      : [...new Set([selfUin, peerUin].filter(Boolean))];

  const tries: Array<{ label: string; fn: () => Promise<unknown> }> = [];
  for (const index of indexOrder.length ? indexOrder : [wallet.stringIndex]) {
    for (const recvUin of recvUinOrder) {
      const base = buildPullDetailBase(wallet, {
        recvUin,
        selfNick,
        chatType,
        peerUid,
        groupId,
        authKey,
        index,
      });
      tries.push(
        { label: `flat/${recvUin}/${String(index).slice(0, 8)}`, fn: () => msgService.pullDetail(base) },
        {
          label: `req/${recvUin}/${String(index).slice(0, 8)}`,
          fn: () => msgService.pullDetail({ pullDetailReq: base }),
        }
      );
      if (authKey && index === authKey) {
        tries.push({
          label: `req+view16/${recvUin}`,
          fn: () =>
            msgService.pullDetail({
              pullDetailReq: { ...base, view: 16, viewtype: 0, src_type: 'web', version: 1 },
            }),
        });
      }
    }
  }

  const out: Array<{ label: string; score: number; recvListLen: number; recvNum: string; error?: string }> =
    [];
  for (const { label, fn } of tries.slice(0, limit)) {
    try {
      const ret = await Promise.race([fn(), sleep(8000).then(() => null)]);
      if (!ret) {
        out.push({ label, score: -1, recvListLen: 0, recvNum: '0', error: 'timeout' });
        continue;
      }
      const rsp = (ret as any)?.pullDetailRsp || ret;
      out.push({
        label,
        score: scorePullDetailRaw(ret),
        recvListLen: extractRecvListFromRaw(ret).length,
        recvNum: String(rsp?.sendOrder?.recvNum ?? '0'),
      });
    } catch (e) {
      out.push({ label, score: -1, recvListLen: 0, recvNum: '0', error: String(e) });
    }
  }
  return out.sort((a, b) => b.score - a.score);
}

/**
 * 拉取红包领取详情 —— 调用 QQ NT 内核 pullDetail
 * 先选最优汇总参数，再基于同一 base 分页拉名单
 */
export async function pullDetail(ctx: any, wallet: Partial<WalletContext> & { billNo: string }) {
  wallet = normalizeWalletFields(wallet);
  wallet = await refreshWalletFromMessage(ctx, wallet);

  const msgService = getMsgService(ctx);
  if (!msgService?.pullDetail) {
    throw new Error('pullDetail API 不可用，请确认 NapCat/QQNT 已登录');
  }
  if (!wallet.pcBody) {
    throw new Error('缺少 pcBody，无法 pullDetail');
  }

  const selfInfo = ctx.core?.selfInfo;
  const selfUin = String(selfInfo?.uin || '');
  const selfNick = String(selfInfo?.nick || selfUin);
  const chatType = Number(wallet.chatType ?? 2);
  const peerUin = String(wallet.peerUin || wallet.peerUid || '');
  const peerUid = String(wallet.peerUid || peerUin);
  const senderUin = String((wallet as any).senderUin || '');
  const authKey = sanitizeAuthKey((wallet as WalletContext).authKey || '');
  const groupId = peerUin;

  // 灰条 jp：authkey；红包本体：stringIndex —— 两者都试，authkey 优先
  const indexOrder = [...new Set([authKey, wallet.stringIndex].filter(Boolean))];
  // 群红包：peerUin(群号) 作 recvUin + 内核字段才能拿到 recvdOrderList
  const recvUinOrder =
    chatType === 2
      ? [...new Set([peerUin, selfUin, senderUin].filter(Boolean))]
      : [...new Set([selfUin, peerUin].filter(Boolean))];

  type Attempt = { label: string; base: Record<string, unknown>; fn: () => Promise<unknown> };
  const attempts: Attempt[] = [];
  for (const index of indexOrder.length ? indexOrder : [wallet.stringIndex]) {
    for (const recvUin of recvUinOrder) {
      const base = buildPullDetailBase(wallet, {
        recvUin,
        selfNick,
        chatType,
        peerUid,
        groupId,
        authKey,
        index,
      });
      attempts.push(
        { label: 'flat', base, fn: () => msgService.pullDetail(base) },
        { label: 'req', base, fn: () => msgService.pullDetail({ pullDetailReq: base }) }
      );
    }
  }

  let lastErr: unknown = null;
  let raw: any = null;
  let bestBase: Record<string, unknown> | null = null;
  let bestScore = -1;
  let mergedClaims: ClaimRecord[] = [];

  const maxAttempts = Math.min(attempts.length, 8);
  for (let i = 0; i < maxAttempts; i++) {
    const { base, fn } = attempts[i];
    try {
      const ret = await Promise.race([fn(), sleep(5000).then(() => null)]);
      if (!ret) continue;
      const score = scorePullDetailRaw(ret);
      if (score < 0) continue;
      const listLen = extractRecvListFromRaw(ret).length;
      mergedClaims = mergeClaims(mergedClaims, normalizeClaims(ret));
      if (score > bestScore || (score === bestScore && listLen > extractRecvListFromRaw(raw).length)) {
        bestScore = score;
        raw = ret;
        bestBase = base;
      }
      if (listLen > 0) break;
    } catch (e) {
      lastErr = e;
    }
  }

  if (!raw || !bestBase) {
    throw lastErr || new Error('pullDetail 无有效返回（可能超时或 pcBody 失效）');
  }

  const summary = parseSendOrderSummary(raw);
  const recvNum = summary?.recvNum || 0;

  // 有汇总但名单空 / 人数不足：再按内核字段补拉领取列表
  if (recvNum > 0 && extractRecvListFromRaw(raw).length === 0) {
    const listed = await pullDetailRecvList(msgService, bestBase, recvNum, selfUin);
    if (listed.claims.length) {
      mergedClaims = mergeClaims(mergedClaims, listed.claims);
      if (listed.listRaw) raw = listed.listRaw;
    }
  } else if (recvNum > 0 && mergedClaims.length > 0 && mergedClaims.length < recvNum) {
    const listed = await pullDetailRecvList(msgService, bestBase, recvNum, selfUin);
    if (listed.claims.length) {
      mergedClaims = mergeClaims(mergedClaims, listed.claims);
      if (listed.listRaw) raw = listed.listRaw;
    }
  }

  const claims = mergedClaims.length ? mergedClaims : normalizeClaims(raw);
  return { claims, summary: summary || parseSendOrderSummary(raw), raw };
}

/** 合并领取记录：按 uin 去重，保留已知金额与较新时间 */
export function mergeClaims(existing: ClaimRecord[], incoming: ClaimRecord[]): ClaimRecord[] {
  const map = new Map<string, ClaimRecord>();
  const keyOf = (c: ClaimRecord) =>
    isValidUin(c.uin) ? `u:${c.uin}` : c.name ? `n:${c.name}` : `t:${c.time}`;
  for (const c of [...existing, ...incoming]) {
    const uin = isValidUin(c.uin) ? String(c.uin) : '';
    const name = String(c.name || '').trim();
    if (!uin && !name) continue;
    // 无有效 QQ、无领取时间、且名字像发包人占位的脏数据，后面由 repair 再清
    const cleaned: ClaimRecord = { ...c, uin, name };
    const k = keyOf(cleaned);
    const prev = map.get(k);
    if (!prev) {
      map.set(k, cleaned);
      continue;
    }
    const useNewTime = (cleaned.time || 0) >= (prev.time || 0);
    // 新名称为空 / 像「多人同名串号」时保留旧昵称（详情 recvName 不可靠）
    const prevName = String(prev.name || '').trim();
    const newName = String(cleaned.name || '').trim();
    let nameOut = newName || prevName;
    if (prevName && newName && prevName !== newName) {
      // 两边都有名：优先保留已有非空差异名，避免被串号发包人昵称覆盖
      // 仅当旧名是占位、或旧名就是 QQ 号时才换新名
      const prevWeak =
        prevName.startsWith('未知领取人') ||
        prevName === prev.uin ||
        prevName === cleaned.uin;
      nameOut = prevWeak ? newName : prevName;
    }
    map.set(k, {
      uin: cleaned.uin || prev.uin,
      name: nameOut,
      amount: cleaned.amount > 0 ? cleaned.amount : prev.amount,
      time: Math.max(cleaned.time || 0, prev.time || 0),
      timeText: useNewTime ? cleaned.timeText || prev.timeText : prev.timeText || cleaned.timeText,
    });
  }
  return Array.from(map.values()).sort((a, b) => (a.time || 0) - (b.time || 0));
}

/** 从 pullDetail 的 sendOrder 提取汇总（QQ 9.9 常无 recvdOrderList） */
export function parseSendOrderSummary(raw: any): RedPacketSummary | undefined {
  const so = raw?.pullDetailRsp?.sendOrder || raw?.sendOrder;
  if (!so) return undefined;
  const totalNum = parseInt(String(so.totalNum ?? '0'), 10) || 0;
  const recvNum = parseInt(String(so.recvNum ?? '0'), 10) || 0;
  if (totalNum <= 0 && recvNum <= 0 && !so.luckyUin) return undefined;
  return {
    totalNum,
    recvNum,
    totalAmount: (parseInt(String(so.totalAmount ?? '0'), 10) || 0) / 100,
    recvAmount: (parseInt(String(so.recvAmount ?? '0'), 10) || 0) / 100,
    luckyUin: isValidUin(so.luckyUin) ? String(so.luckyUin) : '',
    luckyName: String(
      so.luckyName ||
        (isValidUin(so.luckyUin) && String(so.luckyUin) === String(so.sendUin || '')
          ? so.sendName
          : '') ||
        ''
    ),
    wishing: String(so.wishing || ''),
  };
}

/** 灰条「XXX领取了红包」→ 单人领取事件（金额通常未知） */
export function extractGrayTipClaim(wallet: WalletContext): ClaimRecord | null {
  const w = wallet.walletElement;
  if (!w?.fromGrayTip) return null;
  const params = (w.params || {}) as Record<string, string>;
  const parsed = parseGrayTipJsonStr(String(w.jsonStr || ''));
  const graber = String(w.graber || params.graber || params.grabber || parsed.uin || '');
  let name = String(w.graberName || params.graberName || params.nick || params.name || parsed.name || '').trim();
  if (!name && name !== '-') {
    try {
      const items = JSON.parse(String(w.jsonStr || '{}'))?.items || [];
      for (const it of items) {
        if (it?.type === 'qq' && it?.txt) {
          name = String(it.txt).trim();
          break;
        }
        if (it?.type === 'text' && it?.txt && !name) {
          const t = String(it.txt);
          const m = t.match(/^(.+?)领取了/);
          if (m) name = m[1].trim();
        }
      }
    } catch {
      /* ignore */
    }
  }
  const uin = isValidUin(graber) ? graber : isValidUin(parsed.uin) ? parsed.uin : '';
  if (!uin && !name) return null;
  const time = wallet.msgTime || Math.floor(Date.now() / 1000);
  let amount = parsed.amount || 0;
  const amountFen = parseInt(
    String(params.recvamount || params.recvAmount || params.amount || params.money || '0'),
    10
  );
  if (Number.isFinite(amountFen) && amountFen > 0) amount = amountFen / 100;
  return {
    uin,
    name: name || (uin ? uin : '-'),
    amount,
    time,
    timeText: formatTime(time),
  };
}

/** 合并已有记录、pullDetail 与自身领取结果 */
export function combineClaimsFromDetail(
  existing: ClaimRecord[],
  raw: any,
  selfGrab?: { uin: string; name: string; amount: number; time?: number },
  ctx?: { senderUin?: string; senderName?: string }
): { claims: ClaimRecord[]; summary?: RedPacketSummary } {
  let claims = mergeClaims(existing, normalizeClaims(raw));
  if (selfGrab && selfGrab.amount > 0) {
    const t = selfGrab.time || Math.floor(Date.now() / 1000);
    claims = mergeClaims(claims, [
      {
        uin: selfGrab.uin,
        name: selfGrab.name,
        amount: selfGrab.amount,
        time: t,
        timeText: formatTime(t),
      },
    ]);
  }
  claims = inferMissingClaimsFromSummary(claims, raw, ctx);
  claims = inferMissingAmounts(claims, parseSendOrderSummary(raw));
  claims = repairBadClaims({ claims, summary: parseSendOrderSummary(raw), rawDetail: raw });
  return { claims, summary: parseSendOrderSummary(raw) };
}

/** 仅 1 人金额未知且汇总已知时，补剩余金额 */
export function inferMissingAmounts(
  claims: ClaimRecord[],
  summary?: RedPacketSummary
): ClaimRecord[] {
  if (!summary || summary.recvAmount <= 0 || summary.recvNum <= 0) return claims;
  if (claims.length !== summary.recvNum) return claims;
  const unknown = claims.filter((c) => (c.amount || 0) <= 0);
  if (unknown.length !== 1) return claims;
  const known = claims.reduce((s, c) => s + (c.amount > 0 ? c.amount : 0), 0);
  const remain = Math.round((summary.recvAmount - known) * 100) / 100;
  if (remain <= 0) return claims;
  const target = unknown[0];
  return claims.map((c) => (c === target ? { ...c, amount: remain } : c));
}

/** 按汇总补齐领取人数占位（QQ 9.9 常无 recvdOrderList，需靠灰条 + 占位） */
export function ensureClaimSlots(claims: ClaimRecord[], summary?: RedPacketSummary): ClaimRecord[] {
  if (!summary || summary.recvNum <= 0) return claims;
  let next = mergeClaims([], claims);
  let idx = 0;
  while (next.length < summary.recvNum) {
    idx += 1;
    next = mergeClaims(next, [
      { uin: '', name: `未知领取人${idx}`, amount: 0, time: 0, timeText: '' },
    ]);
  }
  return next;
}

/**
 * QQ 9.9 常无 recvdOrderList：仅补占位，不臆造手气最佳/发包人为领取人
 */
export function inferMissingClaimsFromSummary(
  existing: ClaimRecord[],
  raw: any,
  _ctx?: { senderUin?: string; senderName?: string }
): ClaimRecord[] {
  const summary = parseSendOrderSummary(raw);
  if (!summary) return existing;
  let next = mergeClaims([], existing);
  if (summary.recvNum > 1 && isValidUin(summary.luckyUin)) {
    const luckyName =
      summary.luckyName ||
      (_ctx?.senderUin === summary.luckyUin ? _ctx?.senderName : '') ||
      summary.luckyUin;
    next = mergeClaims(next, [
      {
        uin: summary.luckyUin,
        name: luckyName,
        amount: 0,
        time: 0,
        timeText: '',
      },
    ]);
  }
  if (summary.recvNum <= next.length) return next;
  return ensureClaimSlots(next, summary);
}

/** 修复旧缓存：脏 luckyUin=0、人数超标、总金额当单人等 */
export function repairBadClaims(rec: {
  claims?: ClaimRecord[];
  summary?: RedPacketSummary;
  rawDetail?: unknown;
}): ClaimRecord[] {
  const summary = rec.summary || (rec.rawDetail ? parseSendOrderSummary(rec.rawDetail) : undefined);
  let claims = mergeClaims([], rec.claims || []);

  // 丢掉无效 QQ 占位；保留灰条昵称、未知领取人占位、手气最佳
  claims = claims.filter((c) => {
    if (isValidUin(c.uin)) {
      if (summary && c.uin === summary.luckyUin) return true;
      // 无时间、无金额的 QQ 行多为旧版误推断，若还有其他有效行则丢弃
      if ((c.time || 0) <= 0 && (c.amount || 0) <= 0) {
        const others = claims.filter(
          (x) =>
            x !== c &&
            ((x.time || 0) > 0 || (x.amount || 0) > 0 || String(x.name || '').startsWith('未知领取人'))
        );
        if (others.length >= (summary?.recvNum || 1) - 1) return false;
      }
      return true;
    }
    const name = String(c.name || '').trim();
    if (name.startsWith('未知领取人')) return true;
    return (c.time || 0) > 0 && !!name;
  });

  // 已领份数已知时，人数不得超过 recvNum；优先保留有有效 QQ / 有时间的
  if (summary && summary.recvNum > 0 && claims.length > summary.recvNum) {
    claims = [...claims].sort((a, b) => {
      const score = (c: ClaimRecord) =>
        (isValidUin(c.uin) ? 100 : 0) + (c.time > 0 ? 10 : 0) + (c.amount > 0 ? 1 : 0);
      return score(b) - score(a);
    });
    claims = claims.slice(0, summary.recvNum);
  }

  // 单人包却把总领取额记在一人身上（且还有其他人）时的旧逻辑保留
  if (summary && summary.recvNum > 1 && claims.length === 1) {
    const only = claims[0];
    if (only.amount > 0 && summary.recvAmount > 0 && Math.abs(only.amount - summary.recvAmount) < 0.001) {
      return [{ ...only, amount: 0 }];
    }
  }

  // 单人包金额重复：多人金额之和远超 recvAmount 时，只留最佳一条
  if (summary && summary.recvNum === 1 && claims.length > 1) {
    const scored = [...claims].sort((a, b) => {
      const score = (c: ClaimRecord) =>
        (isValidUin(c.uin) ? 100 : 0) + (c.time > 0 ? 10 : 0) + (c.amount > 0 ? 1 : 0);
      return score(b) - score(a);
    });
    claims = [scored[0]];
  }

  claims = ensureClaimSlots(claims, summary);
  claims = inferMissingAmounts(claims, summary);
  // 重排未知领取人序号 1..n
  let unknownIdx = 0;
  claims = claims.map((c) => {
    if (String(c.name || '').startsWith('未知领取人')) {
      unknownIdx += 1;
      return { ...c, name: `未知领取人${unknownIdx}` };
    }
    return c;
  });
  return claims;
}

/** 把各种可能的返回结构归一成 ClaimRecord[] */
export function normalizeClaims(raw: any): ClaimRecord[] {
  const rsp = raw?.pullDetailRsp || raw?.grabRedBagRsp || raw?.detail || raw;
  const lists: any[] = [];

  const candidates = [
    rsp?.recvList,
    rsp?.recvdList,
    rsp?.recv_details,
    rsp?.recvDetails,
    rsp?.detailList,
    rsp?.orderList,
    rsp?.recvdOrderList,
    rsp?.grabedList,
    rsp?.list,
    Array.isArray(rsp) ? rsp : null,
  ];

  for (const c of candidates) {
    if (Array.isArray(c) && c.length) lists.push(...c);
  }

  // 有的版本只有自己的 recvdOrder
  if (!lists.length && rsp?.recvdOrder) lists.push(rsp.recvdOrder);

  const out: ClaimRecord[] = [];
  const pushItem = (item: any) => {
    if (!item || typeof item !== 'object') return;
    let uin = String(
      item.uin ||
        item.user_id ||
        item.recvUin ||
        item.recv_uin ||
        item.luckyUin ||
        item.qq ||
        ''
    );
    if (!isValidUin(uin)) uin = '';
    const name = String(
      item.name ||
        item.nickname ||
        item.nick ||
        item.recvName ||
        item.recv_name ||
        item.luckyName ||
        ''
    );
    const amountFen = parseInt(
      String(item.amount ?? item.recvAmount ?? item.recv_amount ?? '0'),
      10
    );
    const amount = Number.isFinite(amountFen) ? amountFen / 100 : 0;
    let time = Number(item.time || item.recvTime || item.recv_time || item.createTime || 0);
    if (time > 1e12) time = Math.floor(time / 1000);
    if (!uin && !name && !amount) return;
    // 仅有发包人昵称 + 金额、无有效 QQ、无时间 → 不可靠，丢弃
    if (!uin && amount > 0 && time <= 0) return;
    out.push({
      uin,
      name,
      amount,
      time,
      timeText: formatTime(time),
    });
  };

  for (const item of lists) pushItem(item);

  // QQ 9.9.x：recvdOrderList 里 recvName 常全员串成发包人昵称，不可信
  if (out.length >= 2) {
    const named = out.map((c) => String(c.name || '').trim()).filter(Boolean);
    if (named.length >= 2 && named.every((n) => n === named[0])) {
      for (const c of out) c.name = '';
    }
  }

  // QQ 9.9.x：recvdOrderList 常为空；用 sendOrder 补手气最佳 / 单人包
  if (rsp?.sendOrder) {
    const so = rsp.sendOrder;
    const recvNum = parseInt(String(so.recvNum ?? '0'), 10) || 0;
    const lucky = isValidUin(so.luckyUin) ? String(so.luckyUin) : '';
    if (lucky && !out.some((c) => c.uin === lucky)) {
      if (recvNum === 1) {
        pushItem({
          uin: lucky,
          name: so.luckyName || '',
          amount: so.recvAmount || so.totalAmount || 0,
          time: so.createTime || 0,
        });
      } else if (recvNum > 1) {
        pushItem({ uin: lucky, name: so.luckyName || '', amount: 0, time: 0 });
      }
    }
  }

  // 去重（按 uin+amount+time）
  const seen = new Set<string>();
  return out.filter((c) => {
    const k = `${c.uin}|${c.amount}|${c.time}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

export async function sendPasswordIfNeeded(ctx: any, wallet: WalletContext) {
  if (wallet.redChannel !== 32) return true;
  const wording = wallet.wishing;
  if (!wording) return false;
  const groupId = wallet.peerUin;
  await ctx.actions.call(
    'send_group_msg',
    {
      group_id: groupId,
      message: [{ type: 'text', data: { text: wording } }],
    },
    ctx.adapterName,
    ctx.pluginManager.config
  );
  await sleep(500);
  return true;
}
