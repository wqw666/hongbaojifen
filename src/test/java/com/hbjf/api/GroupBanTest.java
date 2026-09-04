package com.hbjf.api;

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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * QQ群封禁测试：open 上报建群(含创建时间) → 管理端封禁(带原因) → 上报更新不改封禁态 → 解封
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class GroupBanTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;
    long groupPkId;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("TRUNCATE TABLE qq_groups");

        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk()).andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();
    }

    private void upsert(String createTime) throws Exception {
        mvc.perform(post("/api/open/groups")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"group_id\":\"70001\",\"group_name\":\"红包总群\",\"owner_qq\":\"10001\","
                                + "\"member_count\":\"66\",\"create_time\":\"" + createTime + "\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void banAndUnbanRoundtrip() throws Exception {
        upsert("2023-05-06 07:08:09");
        Long id = jdbc.queryForObject("SELECT id FROM qq_groups WHERE group_id='70001'", Long.class);
        groupPkId = id;
        // 创建时间入库 + 未上报 admin_qqs 不留脏
        assertThat(jdbc.queryForObject("SELECT create_time FROM qq_groups WHERE id=?", String.class, id))
                .isEqualTo("2023-05-06 07:08:09");

        // 管理端列表带新列
        mvc.perform(get("/api/admin/qq-groups").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].create_time").value("2023-05-06 07:08:09"))
                .andExpect(jsonPath("$.data[0].ban_reason").value(""))
                .andExpect(jsonPath("$.data[0].status").value("active"));

        // 封禁
        mvc.perform(post("/api/admin/qq-groups/" + id + "/ban")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"涉及违规\"}"))
                .andExpect(status().isOk());
        assertThat(jdbc.queryForObject("SELECT status FROM qq_groups WHERE id=?", String.class, id)).isEqualTo("banned");
        assertThat(jdbc.queryForObject("SELECT ban_reason FROM qq_groups WHERE id=?", String.class, id)).contains("违规");

        // agent 继续正常上报群信息（成员数变化），不得解开封禁态
        mvc.perform(post("/api/open/groups")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"group_id\":\"70001\",\"group_name\":\"红包总群\",\"member_count\":\"88\"}"))
                .andExpect(status().isOk());
        assertThat(jdbc.queryForObject("SELECT status FROM qq_groups WHERE id=?", String.class, id)).isEqualTo("banned");

        // 封禁群从正常列表不出现 / 封禁筛选可见
        mvc.perform(get("/api/admin/qq-groups").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1)) // 单表断言用；封禁态保留展示
                .andExpect(jsonPath("$.data[0].status").value("banned"));

        // 解封
        mvc.perform(post("/api/admin/qq-groups/" + id + "/unban")
                        .header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk());
        assertThat(jdbc.queryForObject("SELECT status FROM qq_groups WHERE id=?", String.class, id)).isEqualTo("active");
        assertThat(jdbc.queryForObject("SELECT ban_reason FROM qq_groups WHERE id=?", String.class, id)).isEmpty();

        // 非法格式的 create_time 不覆盖已有值
        upsert("not-a-time");
        assertThat(jdbc.queryForObject("SELECT create_time FROM qq_groups WHERE id=?", String.class, id))
                .isEqualTo("2023-05-06 07:08:09");
    }
}
