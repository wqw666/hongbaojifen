/** 插件配置 */
export interface PluginConfig {
  /** 总开关 */
  enabled: boolean;
  /** 自动领取红包 */
  autoGrab: boolean;
  /** 是否允许领取自己发的拼手气红包（多人红包自己也可抢） */
  grabSelf: boolean;
  /** 领取后自动拉取详情 */
  autoPullDetail: boolean;
  /** 监听的群号列表（空=全部群） */
  watchGroups: string[];
  /** 随机延迟下限 ms */
  delayMin: number;
  /** 随机延迟上限 ms */
  delayMax: number;
  /** 是否处理口令红包（先发口令再领） */
  handlePassword: boolean;
  /** 主人 QQ（通知用，可空） */
  masterQQ: string;
  /** 玩法转发总开关（false=不把群消息推给 agent 玩法引擎） */
  playEnabled: boolean;
  /** 玩法转发群白名单（空=全部群） */
  playGroups: string[];
  /** agent 本地玩法引擎回调地址 */
  playCallback: string;
  /** 玩法最近一次启用时间戳（ms，0=未启用）：只处理此后的红包，历史红包不推 */
  playEnabledAt?: number;
}

export const DEFAULT_CONFIG: PluginConfig = {
  enabled: true,
  autoGrab: true,
  grabSelf: true,
  autoPullDetail: true,
  watchGroups: [],
  delayMin: 800,
  delayMax: 2000,
  handlePassword: true,
  masterQQ: '',
  playEnabled: false,
  playGroups: [],
  playCallback: 'http://127.0.0.1:6101/play/msg',
  playEnabledAt: 0,
};

/** 红包消息里提取到的上下文 */
export interface WalletContext {
  walletElement: any;
  billNo: string;
  peerUid: string;
  peerUin: string;
  senderUin: string;
  senderName: string;
  peerName: string;
  chatType: number;
  msgSeq: string;
  msgTime: number;
  wishing: string;
  pcBody: any;
  stringIndex: any;
  redChannel: number;
  /** 灰条/详情页 authkey（与 stringIndex 可能不同） */
  authKey?: string;
}

/** 单条领取记录 */
export interface ClaimRecord {
  /** 领取人 QQ */
  uin: string;
  /** 昵称（若有） */
  name: string;
  /** 金额（元）；未知时为 0 */
  amount: number;
  /** 领取时间 unix 秒 */
  time: number;
  /** 可读时间 */
  timeText: string;
}

/** pullDetail 返回的汇总（QQ 9.9 常无逐人列表，只有汇总） */
export interface RedPacketSummary {
  totalNum: number;
  recvNum: number;
  totalAmount: number;
  recvAmount: number;
  luckyUin: string;
  luckyName: string;
  wishing: string;
}

/** 落盘的红包记录 */
export interface RedPacketRecord {
  billNo: string;
  groupId: string;
  groupName: string;
  senderUin: string;
  senderName: string;
  wishing: string;
  msgSeq: string;
  msgTime: number;
  chatType: number;
  peerUid: string;
  grabbed: boolean;
  myAmount?: number;
  claims: ClaimRecord[];
  summary?: RedPacketSummary;
  rawDetail?: unknown;
  updatedAt: number;
  /** 领取所需原始字段，便于事后再查 */
  pcBody?: any;
  stringIndex?: any;
  authKey?: string;
  redChannel?: number;
}
