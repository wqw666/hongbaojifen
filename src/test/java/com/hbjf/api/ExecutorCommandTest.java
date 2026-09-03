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

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 执行器命令通道测试：管理端下发 → agent poll(标记 sent) → 回报 done → 查询
 * 覆盖：超时重投递（5分钟）、错 token、跨执行器回报、去重、待执行上限 50
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class ExecutorCommandTest {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    private static final int PENDING_CAP = 50;

    String adminToken;
    long exeId;
    String exeToken;
    int seq = 0;

    @BeforeEach
    void setUp() throws Exception {
        // executor_commands 有 FK 引用 executors，TRUNCATE 会失败，必须先删子表再删父表
        jdbc.update("DELETE FROM executor_commands");
        jdbc.update("DELETE FROM executors");

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();

        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exec-test-" + (++seq) + "\"}"))
                .andExpect(status().isOk()).andReturn();
        JsonNode data = om.readTree(created.getResponse().getContentAsString()).get("data");
        exeId = data.get("id").asLong();
        exeToken = data.get("token_plain").asText();
        assertThat(exeToken).isNotEmpty();
    }

    private long dispatch(String command, String params) throws Exception {
        String body = om.writeValueAsString(
                java.util.Map.of("command", command, "params", params == null ? "" : params));
        MvcResult r = mvc.perform(post("/api/admin/executors/" + exeId + "/commands")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data").get("id").asLong();
    }

    private JsonNode poll() throws Exception {
        MvcResult r = mvc.perform(post("/api/open/executor/commands/poll")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\"}"))
                .andExpect(status().isOk()).andReturn();
        return om.readTree(r.getResponse().getContentAsString()).get("data").get("commands");
    }

    private void report(long id, String status, String message) throws Exception {
        mvc.perform(post("/api/open/executor/commands/" + id + "/result")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"status\":\"" + status + "\",\"message\":\"" + message + "\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void dispatchPollReportChain() throws Exception {
        long id = dispatch("get_status", null);

        // poll 取走并标记 sent
        JsonNode cmds = poll();
        assertThat(cmds.size()).isEqualTo(1);
        assertThat(cmds.get(0).get("id").asLong()).isEqualTo(id);
        assertThat(cmds.get(0).get("command").asText()).isEqualTo("get_status");
        String dbStatus = jdbc.queryForObject(
                "SELECT status FROM executor_commands WHERE id=?", String.class, id);
        assertThat(dbStatus).isEqualTo("sent");

        // 回报 done
        report(id, "done", "ok");
        dbStatus = jdbc.queryForObject("SELECT status FROM executor_commands WHERE id=?", String.class, id);
        assertThat(dbStatus).isEqualTo("done");
        String result = jdbc.queryForObject("SELECT result FROM executor_commands WHERE id=?", String.class, id);
        assertThat(result).isEqualTo("ok");

        // 已回报的命令不会再 poll 到
        assertThat(poll().size()).isEqualTo(0);

        // 管理端列表可见结果
        mvc.perform(get("/api/admin/executors/" + exeId + "/commands")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].id").value(id))
                .andExpect(jsonPath("$.data[0].status").value("done"))
                .andExpect(jsonPath("$.data[0].result").value("ok"));
    }

    @Test
    void pollRequeueAfterTimeout() throws Exception {
        long id = dispatch("get_status", null);
        poll(); // 取走 → sent

        // 模拟 5 分钟未回报：把 dispatched_at 改到 10 分钟前
        String old = LocalDateTime.now().minusMinutes(10).format(FMT);
        jdbc.update("UPDATE executor_commands SET dispatched_at=? WHERE id=?", old, id);

        // 再 poll 应重新投递（重排回 pending 后取走）
        JsonNode cmds = poll();
        assertThat(cmds.size()).isEqualTo(1);
        assertThat(cmds.get(0).get("id").asLong()).isEqualTo(id);

        // 未超时的 sent 不会被重投
        long id2 = dispatch("get_status", null);
        poll();
        JsonNode again = poll();
        assertThat(again.size()).isEqualTo(0);
        String st = jdbc.queryForObject("SELECT status FROM executor_commands WHERE id=?", String.class, id2);
        assertThat(st).isEqualTo("sent");
    }

    @Test
    void pollWrongTokenReturns404() throws Exception {
        mvc.perform(post("/api/open/executor/commands/poll")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"no-such-token\"}"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/open/executor/commands/poll")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void reportWrongExecutorReturns404() throws Exception {
        // 第二个执行器
        MvcResult created = mvc.perform(post("/api/admin/executors")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"exec-other-" + (++seq) + "\"}"))
                .andExpect(status().isOk()).andReturn();
        String otherToken = om.readTree(created.getResponse().getContentAsString())
                .get("data").get("token_plain").asText();

        long id = dispatch("get_status", null);
        // 用别的执行器 token 回报 → 404（命令不属于该执行器）
        mvc.perform(post("/api/open/executor/commands/" + id + "/result")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + otherToken + "\",\"status\":\"done\",\"message\":\"x\"}"))
                .andExpect(status().isNotFound());

        // 不存在的命令 id → 404
        mvc.perform(post("/api/open/executor/commands/999999/result")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"status\":\"done\",\"message\":\"x\"}"))
                .andExpect(status().isNotFound());

        // 非法 status → 400
        mvc.perform(post("/api/open/executor/commands/" + id + "/result")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"token\":\"" + exeToken + "\",\"status\":\"running\",\"message\":\"x\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void dispatchDeduplicatesSameCommand() throws Exception {
        long id1 = dispatch("set_config", "{\"heartbeat_interval_sec\":30}");
        long id2 = dispatch("set_config", "{\"heartbeat_interval_sec\":30}");
        assertThat(id2).isEqualTo(id1); // 相同命令未完成时去重返回原命令

        // params 不同 → 视为不同命令
        long id3 = dispatch("set_config", "{\"heartbeat_interval_sec\":60}");
        assertThat(id3).isNotEqualTo(id1);

        Integer cnt = jdbc.queryForObject("SELECT COUNT(*) FROM executor_commands", Integer.class);
        assertThat(cnt).isEqualTo(2);
    }

    @Test
    void dispatchCapAtFifty() throws Exception {
        // 直接造 50 条 pending
        String now = LocalDateTime.now().format(FMT);
        for (int i = 0; i < PENDING_CAP; i++) {
            jdbc.update("INSERT INTO executor_commands (executor_id, command, params, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    exeId, "fill-" + i, "", "pending", now, now);
        }
        // 第 51 条通过 dispatch → 400
        mvc.perform(post("/api/admin/executors/" + exeId + "/commands")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"command\":\"overflow\",\"params\":\"\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }
}
