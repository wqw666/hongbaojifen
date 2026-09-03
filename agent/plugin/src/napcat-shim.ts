/** 本地类型垫片，避免强依赖 napcat-types 安装 */
export type NapCatPluginContext = any;
export type PluginModule = {
  plugin_init: (ctx: NapCatPluginContext) => Promise<void> | void;
  plugin_onmessage?: (ctx: NapCatPluginContext, event: any) => Promise<void> | void;
  plugin_cleanup?: (ctx: NapCatPluginContext) => Promise<void> | void;
  plugin_get_config?: (ctx: NapCatPluginContext) => Promise<any> | any;
  plugin_set_config?: (ctx: NapCatPluginContext, config: any) => Promise<void> | void;
};
