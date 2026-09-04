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
  if (!config.playEnabled) return false;

  const groupId = String(event?.group_id ?? '');
  const qq = String(event?.user_id ?? event?.userId ?? '');
  if (!groupId || !qq) return false;
  // 双保险：无论如何不把机器人自己当玩家
  if (qq === String(event?.self_id ?? '')) return false;

  const text = extractPlainText(event);
  if (!text) return false;
  if (config.playGroups.length && !config.playGroups.includes(groupId)) return false;

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
      if (typeof body.enabled === 'boolean') config.playEnabled = body.enabled;
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
      if (!config.playEnabled) return fail(res, '玩法已停用');
      const body = req.body || {};
      const groupId = String(body.group_id ?? '');
      const text = String(body.text ?? '').trim();
      if (!groupId || !text) return fail(res, '缺少 group_id/text');
      if (config.playGroups.length && !config.playGroups.includes(groupId)) {
        return fail(res, '群不在玩法名单');
      }
      const atQq =
        body.at_qq !== undefined && body.at_qq !== null && String(body.at_qq) !== ''
          ? String(body.at_qq)
          : '';
      const message: any[] = [];
      if (atQq) message.push({ type: 'at', data: { qq: atQq } });
      message.push({ type: 'text', data: { text } });
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
}
