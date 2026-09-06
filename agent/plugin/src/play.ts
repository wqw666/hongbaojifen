/**
 * 游戏玩法（群聊自动回复）模块：把群消息转发给 agent 玩法引擎，并提供回复发送端点。
 *
 * 链路：
 *   QQ 群消息 → plugin_onmessage → handlePlayMessage（playEnabled 且群在白名单、
 *   纯文本、非自己发言）→ POST agent 本地回调 playCallback（默认
 *   http://127.0.0.1:6101/play/msg）→ agent 玩法引擎按玩法 .py 规则算回复
 *   → POST 本模块 /play/reply {group_id, at_qq, text} → actions.call
 *   send_group_msg（自动 @ at_qq）→ 群里出现机器人回复
 *
 * 约定：
 * - 独立文件，不动红包抓取（api.ts/redpacket.ts）核心逻辑
 * - 配置字段 playEnabled/playGroups/playCallback 挂在共用 config.json
 * - 转发静默失败（agent 未监听 = 玩法没开，丢弃即可，不刷日志）
 * - 自己发的消息（post_type=message_sent）不转发，避免 n+1 玩法回环自触发
 */
import type { NapCatPluginContext } from './napcat-shim.js';
import { config, saveConfig } from './store.js';

export const PLAY_CALLBACK_DEFAULT = 'http://127.0.0.1:6101/play/msg';
const FORWARD_TIMEOUT_MS = 2000;

/** 已推送过「领完」最终状态的红包单号（轮询去重用，避免重复拉详情）。 */
const finalPushedBills = new Set<string>();

export function isFinalPushed(bill: string): boolean {
  return finalPushedBills.has(String(bill));
}

function ok(res: any, data: unknown) {
  res.json({ code: 0, message: 'ok', data });
}

function fail(res: any, message: string, code = -1, status = 400) {
  res.status(status).json({ code, message, data: null });
}

/** 从消息事件提取纯文本（text 段拼接；at/图片/表情等忽略）。 */
export function extractPlainText(event: any): string {
  const segs: any[] = Array.isArray(event?.message) ? event.message : [];
  const parts: string[] = [];
  for (const s of segs) {
    if (s?.type === 'text' && s.data?.text) parts.push(String(s.data.text));
  }
  if (parts.length) return parts.join('').trim();
  const raw = event?.raw_message ?? event?.rawMessage;
  return typeof raw === 'string' ? raw.trim() : '';
}

/** 群消息 → agent 玩法引擎回调。返回是否实际转发。 */
export async function handlePlayMessage(ctx: NapCatPluginContext, event: any): Promise<boolean> {
  // 只转发别人发的群消息；message_sent（自己发）不转发，防回环
  if (event?.post_type !== 'message') return false;
  if (event?.message_type !== 'group') return false;

  const groupId = String(event?.group_id ?? '');
  const qq = String(event?.user_id ?? event?.userId ?? '');
  if (!groupId || !qq) return false;
  // 双保险：无论如何不把机器人自己当玩家
  if (qq === String(event?.self_id ?? '')) return false;

  const text = extractPlainText(event);
  if (!text) return false;
  // 放行条件：玩法开启且群在玩法名单；或群在监控名单（基础玩法常驻：上/下申请/无效指令/局外下注提示）
  const playOk = config.playEnabled
    && (!config.playGroups.length || config.playGroups.includes(groupId));
  const watchOk = (config.watchGroups || []).includes(groupId);
  if (!playOk && !watchOk) return false;

  const url = config.playCallback || PLAY_CALLBACK_DEFAULT;
  const nickname = String(event?.sender?.nickname ?? event?.nickname ?? '');
  try {
    await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ group_id: groupId, qq, nickname, text }),
      signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
    });
  } catch {
    /* agent 未监听/玩法未启用：丢弃即可 */
  }
  return true;
}

/** 上/下积分申请（上分/下分）：独立于玩法总开关，总是转发到 agent 审批页（/play/approve）。
 *  玩家发「上1000」/「下100」申请；旧词「上分1000」/「下分100」仍兼容。 */
export function handleApproveMessage(ctx: NapCatPluginContext, event: any): boolean {
  if (event?.post_type !== 'message') return false;
  if (event?.message_type !== 'group') return false;
  const groupId = String(event?.group_id ?? '');
  const qq = String(event?.user_id ?? event?.userId ?? '');
  if (!groupId || !qq) return false;
  if (qq === String(event?.self_id ?? '')) return false;
  const text = extractPlainText(event);
  const m = /^(上|下)(?:分)?\s*(\d+)$/.exec(text);
  if (!m) return false;
  // 群过滤：玩法群优先，否则监控群，都没有则全部
  const allow = config.playGroups.length ? config.playGroups : (config.watchGroups || []);
  if (allow.length && !allow.includes(groupId)) return false;
  const base = config.playCallback || PLAY_CALLBACK_DEFAULT;
  let url: string;
  try {
    url = new URL('approve', base).toString();
  } catch {
    return false;
  }
  fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      kind: 'approve', group_id: groupId, qq,
      nickname: String(event?.sender?.nickname ?? event?.nickname ?? ''),
      action: m[1] === '上' ? 'up' : 'down',
      amount: Number(m[2]),
      msg_time: Number(event?.time || 0), // QQ 消息发送时间（秒）→ agent 审批页显示申请时间
    }),
    signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
  }).catch(() => {});
  return true;
}

/** 玩法相关路由（挂在 plugin HTTP 同一路由树上）。 */
export function registerPlayRoutes(ctx: NapCatPluginContext) {
  const router = ctx.router;

  // 玩法转发配置状态
  router.getNoAuth('/play/status', (_req: any, res: any) => {
    ok(res, {
      enabled: !!config.playEnabled,
      groups: config.playGroups,
      callback: config.playCallback,
    });
  });

  // 更新玩法转发配置（GUI 玩法页调用）
  router.postNoAuth('/play/config', async (req: any, res: any) => {
    try {
      const body = req.body || {};
      if (typeof body.enabled === 'boolean') {
        if (body.enabled && !config.playEnabled) {
          // 重新启用：只处理此后的红包，开启前的历史红包一律不推
          config.playEnabledAt = Date.now();
        }
        config.playEnabled = body.enabled;
        if (!body.enabled) config.playEnabledAt = 0;
      }
      if (Array.isArray(body.groups)) {
        config.playGroups = body.groups
          .map((g: unknown) => String(g).trim())
          .filter(Boolean);
      }
      if (typeof body.callback === 'string' && body.callback.trim()) {
        config.playCallback = body.callback.trim();
      }
      saveConfig();
      ctx.logger?.info?.(
        `[玩法] 转发配置已更新 enabled=${config.playEnabled} groups=${JSON.stringify(config.playGroups)}`
      );
      ok(res, {
        enabled: !!config.playEnabled,
        groups: config.playGroups,
        callback: config.playCallback,
      });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  // agent 玩法引擎回填回复：send_group_msg，自动 @ at_qq
  router.postNoAuth('/play/reply', async (req: any, res: any) => {
    try {
      const body = req.body || {};
      const groupId = String(body.group_id ?? '');
      const text = String(body.text ?? '').trim();
      if (!groupId || !text) return fail(res, '缺少 group_id/text');
      // 放行：玩法开启且群在玩法名单；或群在监控名单（基础玩法回复）
      const playOk = config.playEnabled
        && (!config.playGroups.length || config.playGroups.includes(groupId));
      const watchOk = (config.watchGroups || []).includes(groupId);
      if (!playOk && !watchOk) {
        return fail(res, '群不在玩法/监控名单');
      }
      const atQq =
        body.at_qq !== undefined && body.at_qq !== null && String(body.at_qq) !== ''
          ? String(body.at_qq)
          : '';
      const message: any[] = [];
      if (atQq) message.push({ type: 'at', data: { qq: atQq } });
      // @ 与正文之间必须有空格，否则 QQ 客户端不解析 @
      message.push({ type: 'text', data: { text: ' ' + text } });
      await ctx.actions.call(
        'send_group_msg',
        { group_id: groupId, message },
        ctx.adapterName,
        ctx.pluginManager.config
      );
      ctx.logger?.info?.(
        `[玩法] 群 ${groupId} 回复 ${atQq || '(全群)'}: ${text.slice(0, 60)}`
      );
      ok(res, { sent: true, group_id: groupId, at_qq: atQq, text });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });

  // agent 玩法引擎 @全体 播报（红包领完结算等）
  router.postNoAuth('/play/announce', async (req: any, res: any) => {
    try {
      if (!config.playEnabled) return fail(res, '玩法已停用');
      const body = req.body || {};
      const groupId = String(body.group_id ?? '');
      const text = String(body.text ?? '').trim();
      if (!groupId || !text) return fail(res, '缺少 group_id/text');
      if (config.playGroups.length && !config.playGroups.includes(groupId)) {
        return fail(res, '群不在玩法名单');
      }
      // 优先 @全体；被内核拒绝（如频率限制/权限）时降级为普通文本发送，保证公告一定送达
      const attempts: any[][] = [
        [{ type: 'at', data: { qq: 'all' } }, { type: 'text', data: { text: ' ' + text } }],
        [{ type: 'text', data: { text: '@全体成员 ' + text } }],
      ];
      let sent = false;
      let lastErr = '';
      for (const message of attempts) {
        try {
          await ctx.actions.call(
            'send_group_msg',
            { group_id: groupId, message },
            ctx.adapterName,
            ctx.pluginManager.config
          );
          sent = true;
          break;
        } catch (e) {
          lastErr = String(e);
          ctx.logger?.warn?.(`[玩法] 群 ${groupId} 发送失败（尝试下一种方式）: ${lastErr.slice(0, 160)}`);
        }
      }
      if (!sent) return fail(res, lastErr, -1, 500);
      ctx.logger?.info?.(`[玩法] 群 ${groupId} @全体: ${text.slice(0, 60)}`);
      ok(res, { sent: true, group_id: groupId, text });
    } catch (e) {
      fail(res, String(e), -1, 500);
    }
  });
}

/** 红包领取事件 → agent 玩法引擎（红包计分玩法）。静默失败。 */
export function notifyRedPacket(ctx: NapCatPluginContext, payload: Record<string, unknown>): void {
  if (!config.playEnabled) return;
  const groupId = String(payload.group_id ?? '');
  if (!groupId) return;
  if (config.playGroups.length && !config.playGroups.includes(groupId)) return;
  // 开启玩法之前的红包不推（历史数据不回复）
  const enabledAt = Number(config.playEnabledAt || 0);
  if (enabledAt) {
    const mt = Number(payload.msg_time || 0);
    if (mt && mt * 1000 < enabledAt) return;
  }
  const base = config.playCallback || PLAY_CALLBACK_DEFAULT;
  let url: string;
  try {
    url = new URL('redpacket', base).toString();
  } catch {
    return;
  }
  const total = Number(payload.total_num || 0);
  const recv = Number(payload.recv_num || 0);
  if (total > 0 && recv >= total) {
    finalPushedBills.add(String(payload.bill_no || ''));
  }
  fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
  }).catch(() => {
    /* agent 未监听：丢弃 */
  });
}
