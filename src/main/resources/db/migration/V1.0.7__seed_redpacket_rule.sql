-- ========== 红包积分管理系统 V1.0.7：红包玩法种子（原 V1.0.3 编号与既有迁移冲突，重排补执行） ==========
-- rule_redpacket：管理员/群主发红包 → 成员领取自动计分（金额各位数之和，
-- 1.11 元=3 分、0.15=6 分），领完 agent 自动 @全体 结算；聊天消息不回复。
-- 玩法文件本体随 jar 走（classpath:seed_rules/rule_redpacket.py），由
-- PlayRuleService.ensureSeedRules()（@PostConstruct）首次启动落盘到 rules-dir。
--
-- 幂等：同名玩法已存在（老库自建/重传过）则跳过，绝不覆盖业务数据；
-- file_size 与 seed_rules/rule_redpacket.py 字节数一致（1184）。

INSERT INTO play_rule_files (name, description, file_name, file_size, version, status, created_at, updated_at)
SELECT 'rule_redpacket',
       '红包玩法：管理员发红包，成员领取自动计分（金额各位数之和，如1.11元=3分、0.15=6分），领完自动@全体结算。聊天消息不回复。',
       'seed_rule_redpacket.py', 1184, '1.0', 'active', NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM play_rule_files WHERE name = 'rule_redpacket');
