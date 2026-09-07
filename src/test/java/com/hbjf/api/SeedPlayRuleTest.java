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
 * 默认玩法种子（classpath:seed_rules/rule_fuhe.py + PlayRuleService.ensureSeedRules）
 * 任何环境部署即有唯一玩法「复合玩法」，且 open 下载能取到文件内容。
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
        // 玩法文件随 jar 内置（内容与 agent/play_rules/rule_fuhe.py 同源，UTF-8）
        byte[] b = new ClassPathResource("seed_rules/rule_fuhe.py").getInputStream().readAllBytes();
        assertThat(b).isNotEmpty();
        assertThat(new String(b, StandardCharsets.UTF_8)).contains("handle_message");
        assertThat(b.length).isEqualTo(32707); // 与 agent/play_rules/rule_fuhe.py 字节数一致
    }

    @Test
    void seedFilesAreLaidDownAndDownloadable() throws Exception {
        // Bean 构造时 @PostConstruct 已把种子落盘；盘上文件可能残留旧构建 → 先删，ensureSeedRules 会重落新内容
        File f = new File("target/test-rules/seed_rule_fuhe.py");
        java.nio.file.Files.deleteIfExists(f.toPath());
        byte[] expected = new ClassPathResource("seed_rules/rule_fuhe.py").getInputStream().readAllBytes();

        // @BeforeEach 已 TRUNCATE，再触发 ensureSeedRules 让兜底插入「复合玩法」元数据行并落盘新文件
        playRuleService.ensureSeedRules();
        assertThat(f).exists();
        Long id = jdbc.queryForObject("SELECT MAX(id) FROM play_rule_files", Long.class);
        assertThat(jdbc.queryForObject("SELECT name FROM play_rule_files WHERE id=?", String.class, id))
                .isEqualTo("复合玩法");

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
                new File("target/test-rules/seed_rule_fuhe.py").toPath());
        playRuleService.ensureSeedRules();
        byte[] after = java.nio.file.Files.readAllBytes(
                new File("target/test-rules/seed_rule_fuhe.py").toPath());
        assertThat(after).isEqualTo(before);
        // 元数据行同样幂等：active「复合玩法」已存在则不重复插入
        playRuleService.ensureSeedRules();
        Integer cnt = jdbc.queryForObject(
                "SELECT COUNT(*) FROM play_rule_files WHERE name='复合玩法' AND status='active'", Integer.class);
        assertThat(cnt).isEqualTo(1);
    }
}
