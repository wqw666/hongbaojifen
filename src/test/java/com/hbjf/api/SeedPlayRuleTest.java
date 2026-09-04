package com.hbjf.api;

import com.hbjf.api.service.PlayRuleService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import java.io.File;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 默认玩法种子（V1.0.2 迁移 + classpath:seed_rules/ + PlayRuleService.ensureSeedRules）
 * 任何环境部署即有 rule_add1/rule_add2，且 open 下载能取到文件内容。
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class SeedPlayRuleTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    PlayRuleService playRuleService;

    @BeforeEach
    void clean() {
        jdbc.update("TRUNCATE TABLE play_rule_files");
    }

    @Test
    void seedResourcesOnClasspath() throws Exception {
        // 玩法文件随 jar 内置（内容与 agent/play_rules 样本同源，UTF-8）
        byte[] b1 = new ClassPathResource("seed_rules/rule_add1.py").getInputStream().readAllBytes();
        byte[] b2 = new ClassPathResource("seed_rules/rule_add2.py").getInputStream().readAllBytes();
        assertThat(b1).isNotEmpty();
        assertThat(b2).isNotEmpty();
        assertThat(new String(b1, StandardCharsets.UTF_8)).contains("handle_message");
        assertThat(b1.length).isEqualTo(633); // 与 V1.0.2 迁移 file_size 一致
        assertThat(b2.length).isEqualTo(678);
    }

    @Test
    void seedFilesAreLaidDownAndDownloadable() throws Exception {
        // Bean 构造时 @PostConstruct 已把种子文件落盘到 rules-dir（target/test-rules）
        File f = new File("target/test-rules/seed_rule_add1.py");
        assertThat(f).exists();
        byte[] expected = new ClassPathResource("seed_rules/rule_add1.py").getInputStream().readAllBytes();

        // 模拟迁移插入的元数据行（生产由 V1.0.2 负责；测试上下文不跑 Flyway）
        jdbc.update("INSERT INTO play_rule_files (name, description, file_name, file_size, version, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                "rule_add1", "测试玩法1", "seed_rule_add1.py", expected.length, "1.0", "active", "2026-01-01 00:00:00", "2026-01-01 00:00:00");
        Long id = jdbc.queryForObject("SELECT MAX(id) FROM play_rule_files", Long.class);

        byte[] body = mvc.perform(get("/api/open/rules/" + id + "/download")
                        .header("X-Api-Key", "test-open-key"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsByteArray();
        assertThat(body).isEqualTo(expected); // 下载内容 == classpath 种子文件
    }

    @Test
    void ensureSeedRulesIdempotent() throws Exception {
        // 文件已存在时再次触发不覆盖、不报错（用户重传替换后也不会被重置）
        byte[] before = java.nio.file.Files.readAllBytes(
                new File("target/test-rules/seed_rule_add2.py").toPath());
        playRuleService.ensureSeedRules();
        byte[] after = java.nio.file.Files.readAllBytes(
                new File("target/test-rules/seed_rule_add2.py").toPath());
        assertThat(after).isEqualTo(before);
    }
}
