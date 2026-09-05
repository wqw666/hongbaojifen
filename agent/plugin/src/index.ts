import type { PluginModule } from './napcat-shim.js';
import { config, initStore, saveConfig } from './store.js';
import { onGrabRedBagNotify, onRawRedPacketMessage, onRedPacketMessage, registerRoutes } from './api.js';
import { attachGrabRedBagListener, attachKernelMsgListener, hookPullDetailForDebug, setPullDetailCaptureDir } from './redpacket.js';
import { sweepRedPacketPlay } from './api.js';
import { handleApproveMessage, handlePlayMessage, registerPlayRoutes } from './play.js';

let detachKernel: (() => void) | null = null;
let detachGrab: (() => void) | null = null;
let sweepTimer: ReturnType<typeof setInterval> | null = null;

export const plugin_init: PluginModule['plugin_init'] = async (ctx) => {
  initStore(ctx.dataPath, ctx.configPath);
  setPullDetailCaptureDir(ctx.dataPath);
  registerRoutes(ctx);
  registerPlayRoutes(ctx);

  detachKernel = attachKernelMsgListener(ctx, (raw) => onRawRedPacketMessage(ctx, raw));
  detachGrab = attachGrabRedBagListener(ctx, (info) => onGrabRedBagNotify(ctx, info));
  // 玩法红包轮询：普通红包无领取灰条，定时刷新未领完红包并推送领取变化
  sweepTimer = setInterval(() => {
    sweepRedPacketPlay(ctx).catch(() => {});
  }, 25000);
  try {
    hookPullDetailForDebug(ctx);
  } catch {
    /* 调试钩子可选 */
  }

  ctx.logger?.info?.(
    `[红包监控] 已启动 | autoGrab=${config.autoGrab} autoPullDetail=${config.autoPullDetail}`
  );
  ctx.logger?.info?.('[红包监控] HTTP: /plugin/napcat-plugin-cleaner/api/query');
};

export const plugin_onmessage: PluginModule['plugin_onmessage'] = async (ctx, event) => {
  // 别人发的是 message；自己发的是 message_sent —— 都要处理（红包两者都看；玩法只看 message）
  const pt = event?.post_type;
  if (pt !== 'message' && pt !== 'message_sent') return;
  try {
    await onRedPacketMessage(ctx, event);
    await handlePlayMessage(ctx, event);
    handleApproveMessage(ctx, event);
  } catch (e) {
    ctx.logger?.error?.('[红包监控] 处理消息异常', e);
  }
};

export const plugin_cleanup: PluginModule['plugin_cleanup'] = async (ctx) => {
  try {
    detachKernel?.();
    detachGrab?.();
    if (sweepTimer) clearInterval(sweepTimer);
  } catch {
    /* ignore */
  }
  detachKernel = null;
  detachGrab = null;
  sweepTimer = null;
  ctx.logger?.info?.('[红包监控] 已卸载');
};

export const plugin_get_config: PluginModule['plugin_get_config'] = async () => config;

export const plugin_set_config: PluginModule['plugin_set_config'] = async (_ctx, newConfig) => {
  Object.assign(config, newConfig || {});
  if (typeof (config as any).watchGroups === 'string') {
    config.watchGroups = String((config as any).watchGroups)
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  saveConfig();
};
