import fs from 'fs';
import path from 'path';
import type { PluginConfig, RedPacketRecord } from './types.js';
import { DEFAULT_CONFIG } from './types.js';
import { mergeClaims, parseSendOrderSummary, repairBadClaims, normalizeBinaryField, normalizeStringIndex } from './redpacket.js';

let dataDir = '';
let configPath = '';
let storePath = '';

export let config: PluginConfig = { ...DEFAULT_CONFIG };
export const records = new Map<string, RedPacketRecord>();

export function initStore(ctxDataPath: string, ctxConfigPath?: string) {
  dataDir = ctxDataPath || path.join(process.cwd(), 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  configPath = ctxConfigPath || path.join(dataDir, 'config.json');
  storePath = path.join(dataDir, 'redpackets.json');
  loadConfig();
  loadRecords();
}

export function getDataDir() {
  return dataDir;
}

export function loadConfig() {
  try {
    if (fs.existsSync(configPath)) {
      const raw = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      Object.assign(config, DEFAULT_CONFIG, raw);
      if (typeof (config as any).watchGroups === 'string') {
        config.watchGroups = String((config as any).watchGroups)
          .split(/[,，\s]+/)
          .map((s) => s.trim())
          .filter(Boolean);
      }
    } else {
      saveConfig();
    }
  } catch {
    /* keep defaults */
  }
}

export function saveConfig() {
  try {
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf-8');
  } catch {
    /* ignore */
  }
}

function loadRecords() {
  try {
    if (!fs.existsSync(storePath)) return;
    const list: RedPacketRecord[] = JSON.parse(fs.readFileSync(storePath, 'utf-8'));
    records.clear();
    let dirty = false;
    for (const r of list) {
      if (!r?.billNo) continue;
      const summary = r.summary || parseSendOrderSummary(r.rawDetail);
      const claims = repairBadClaims({ ...r, summary });
      const pcBody = normalizeBinaryField(r.pcBody);
      const stringIndex = normalizeStringIndex(r.stringIndex);
      if (pcBody !== r.pcBody || stringIndex !== r.stringIndex) dirty = true;
      records.set(r.billNo, {
        ...r,
        claims,
        summary: summary || r.summary,
        pcBody,
        stringIndex,
      });
    }
    if (dirty) saveRecords();
  } catch {
    /* ignore */
  }
}

export function saveRecords() {
  try {
    const list = Array.from(records.values()).sort((a, b) => b.msgTime - a.msgTime);
    // 最多保留 2000 条
    const trimmed = list.slice(0, 2000);
    fs.writeFileSync(storePath, JSON.stringify(trimmed, null, 2), 'utf-8');
  } catch {
    /* ignore */
  }
}

export function upsertRecord(partial: RedPacketRecord) {
  const prev = records.get(partial.billNo);
  let claims = partial.claims;
  if (claims?.length) {
    claims = mergeClaims(prev?.claims || [], claims);
  } else {
    claims = prev?.claims || [];
  }
  claims = repairBadClaims({
    claims,
    summary: partial.summary || prev?.summary || parseSendOrderSummary(partial.rawDetail ?? prev?.rawDetail),
    rawDetail: partial.rawDetail ?? prev?.rawDetail,
  });
  const next: RedPacketRecord = {
    ...prev,
    ...partial,
    claims,
    summary: partial.summary || prev?.summary || parseSendOrderSummary(partial.rawDetail ?? prev?.rawDetail),
    pcBody: normalizeBinaryField(partial.pcBody ?? prev?.pcBody),
    stringIndex: normalizeStringIndex(partial.stringIndex ?? prev?.stringIndex),
    updatedAt: Date.now(),
  };
  records.set(next.billNo, next);
  saveRecords();
  return next;
}

/** 按群 + 时间范围查询（红包一般只有一个，返回列表） */
export function queryByGroupAndTime(groupId: string, startSec: number, endSec: number): RedPacketRecord[] {
  const gid = String(groupId);
  return Array.from(records.values())
    .filter((r) => String(r.groupId) === gid && r.msgTime >= startSec && r.msgTime <= endSec)
    .sort((a, b) => a.msgTime - b.msgTime);
}

export function getRecord(billNo: string) {
  return records.get(billNo);
}

/** 清空所有已缓存的红包监控记录 */
export function clearAllRecords(): number {
  const n = records.size;
  records.clear();
  saveRecords();
  return n;
}
