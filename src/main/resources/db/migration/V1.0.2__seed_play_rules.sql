-- ========== 红包积分管理系统 V1.0.2：默认玩法种子 ==========
-- 任何环境部署（新库）即有 rule_add1（数字+1）/ rule_add2（数字+2）两个已启用玩法，
-- agent 拉取后即可在群内直接玩 —— 满足「初始默认配置即带两个测试玩法」。
--
-- 本迁移只插元数据：玩法文件本体随 jar 走（classpath:seed_rules/rule_add{1,2}.py），
-- 由 PlayRuleService.ensureSeedRules()（@PostConstruct）首次启动时落盘到 rules-dir；
-- 纯 SQL 无法写磁盘文件，二者配合才构成完整可用玩法。
--
-- 幂等：同名玩法已存在（老库自建/重传过）则跳过，绝不覆盖业务数据；
-- file_size 与 seed_rules 文件字节数一致（633/678），列表页展示准确。
-- 时间列全库 VARCHAR(19) 'yyyy-MM-dd HH:mm:ss'，NOW() 隐式转字符串即可。

INSERT INTO play_rule_files (name, description, file_name, file_size, version, status, created_at, updated_at)
SELECT 'rule_add1', '测试玩法1：收到纯数字 → 回复数字+1；发非数字/空内容不回复。', 'seed_rule_add1.py', 633, '1.0', 'active', NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM play_rule_files WHERE name = 'rule_add1');

INSERT INTO play_rule_files (name, description, file_name, file_size, version, status, created_at, updated_at)
SELECT 'rule_add2', '测试玩法2：收到纯数字 → 回复数字+2；发非数字内容 → 回复「请输入数字~」。', 'seed_rule_add2.py', 678, '1.0', 'active', NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM play_rule_files WHERE name = 'rule_add2');
