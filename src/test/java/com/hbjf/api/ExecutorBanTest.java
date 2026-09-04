package com.hbjf.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 执行器封禁测试：双按钮（仅封禁 / 封禁并重置token）+ 解封 + 封禁态拒绝所有带 token 写操作
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ExecutorBanTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    long exeId;
    String exeToken;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("DELETE FROM executor_commands");
        jdbc.update("DELETE FROM executors");
        jdbc.update("TRUNCATE TABLE qq_accounts");

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exe-ban-1\",\"host\":\"agent-1\"}"))
                .andExpect(status().isOk()).andReturn();
        JsonNode data = om.readTree(created.getResponse().getContentAsString()).get("data");
        exeId = data.get("id").asLong();
        exeToken = data.get("token_plain").asText();
    }

    private void heartbeat(String token) throws Exception {
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + token + "\",\"host\":\"agent-1\",\"version\":\"1.0\","
                                + "\"admin_qq\":\"91001\",\"admin_nickname\":\"操盘员\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    private void ban(String reason, boolean reset) throws Exception {
        mvc.perform(post("/api/admin/executors/" + exeId + "/ban")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"" + reason + "\",\"reset_token\":" + reset + "}"))
                .andExpect(status().isOk());
    }

    @Test
    void banWithoutResetThenUnban() throws Exception {
        heartbeat(exeToken); // 心跳绑定管理员QQ，正常
        assertThat(jdbc.queryForObject("SELECT last_ip FROM executors WHERE id=?", String.class, exeId)).isNotEmpty();

        ban("疑似被攻破", false);
        assertThat(jdbc.queryForObject("SELECT banned_at FROM executors WHERE id=?", String.class, exeId)).isNotEmpty();
        assertThat(jdbc.queryForObject("SELECT ban_reason FROM executors WHERE id=?", String.class, exeId)).contains("攻破");

        // 封禁态：心跳/命令poll/批量注册全部被拒 40310
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\"}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40310));
        mvc.perform(post("/api/open/executor/commands/poll")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\"}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40310));
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30001\"}]}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40310));

        // 解封恢复（token 未变，agent 无需重新配置）
        mvc.perform(post("/api/admin/executors/" + exeId + "/unban")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk());
        assertThat(jdbc.queryForObject("SELECT banned_at FROM executors WHERE id=?", String.class, exeId)).isEmpty();
        heartbeat(exeToken);
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"executor_token\":\"" + exeToken + "\",\"members\":[{\"qq\":\"30001\"}]}"))
                .andExpect(status().isOk());
    }

    @Test
    void banWithResetToken() throws Exception {
        heartbeat(exeToken);
        String tokenBefore = jdbc.queryForObject("SELECT token FROM executors WHERE id=?", String.class, exeId);

        ban("credential 泄露", true);
        String tokenAfter = jdbc.queryForObject("SELECT token FROM executors WHERE id=?", String.class, exeId);
        assertThat(tokenAfter).isNotEqualTo(tokenBefore);

        // 旧 token 立即失效（已不存在 → 404）
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\"}"))
                .andExpect(status().isNotFound());
        // 新 token 也处于封禁中 → 40310
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + tokenAfter + "\"}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value(40310));

        // 解封后仅新 token 可用
        mvc.perform(post("/api/admin/executors/" + exeId + "/unban")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk());
        mvc.perform(post("/api/open/executor/heartbeat")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\"}"))
                .andExpect(status().isNotFound());
        heartbeat(tokenAfter);
    }

    @Test
    void bannedExecutorFilteredInAdminList() throws Exception {
        heartbeat(exeToken);
        ban("x", false);
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .get("/api/admin/executors").param("status", "banned")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].id").value(exeId))
                .andExpect(jsonPath("$.data[0].admin_qq").value("91001"));
    }

    @Test
    void staleHeartbeatAutoOffline() throws Exception {
        heartbeat(exeToken);
        assertThat(jdbc.queryForObject("SELECT status FROM executors WHERE id=?", String.class, exeId))
                .isEqualTo("online");
        // 心跳超时（> 默认 30s 阈值）：管理列表查询即自动判离线
        jdbc.update("UPDATE executors SET last_heartbeat='2020-01-01 00:00:00' WHERE id=?", exeId);
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .get("/api/admin/executors").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].id").value(exeId))
                .andExpect(jsonPath("$.data[0].status").value("offline"));
        // 新心跳 → 恢复在线
        heartbeat(exeToken);
        assertThat(jdbc.queryForObject("SELECT status FROM executors WHERE id=?", String.class, exeId))
                .isEqualTo("online");
    }
}
