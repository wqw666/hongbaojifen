package com.hbjf.api;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * agent 对接开放接口测试：群上报 / QQ号管理 / 批量注册会员 / 批量查积分
 * 需 X-Api-Key（test-open-key）
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class OpenManageTest {

    private static final String KEY = "test-open-key";

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeEach
    void clean() {
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
        jdbc.update("TRUNCATE TABLE qq_groups");
        jdbc.update("TRUNCATE TABLE qq_accounts");
    }

    private static String body(String json) {
        return json.replace('\'', '"');
    }

    @Test
    void groupUpsertCreatesThenUpdates() throws Exception {
        mvc.perform(post("/api/open/groups")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'group_id':'88888','group_name':'测试群','owner_qq':'10001','member_count':'120'}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created").value(true));

        mvc.perform(post("/api/open/groups")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'group_id':'88888','group_name':'测试群2','admin_qqs':'10001,10002'}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created").value(false));

        Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM qq_groups", Integer.class);
        assertThat(c).isEqualTo(1); // upsert 不重复建行
        String name = jdbc.queryForObject("SELECT group_name FROM qq_groups WHERE group_id='88888'", String.class);
        assertThat(name).isEqualTo("测试群2");
        // 未上报的 owner_qq 不被覆盖（COALESCE(NULLIF) 防空串）；member_count 更新失败时保留
    }

    @Test
    void qqAccountUpsertListDelete() throws Exception {
        mvc.perform(post("/api/open/qq-accounts")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'qq':'10001','type':'admin_qq','nickname':'机器人','remark':'agent登录号'}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created").value(true));

        // 重复上报幂等
        mvc.perform(post("/api/open/qq-accounts")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'qq':'10001','type':'admin_qq','nickname':'机器人2'}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created").value(false));

        mvc.perform(get("/api/open/qq-accounts").param("type", "admin_qq").header("X-Api-Key", KEY))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].qq").value("10001"))
                .andExpect(jsonPath("$.data[0].nickname").value("机器人2"));

        // 普通类型列表不含 admin
        mvc.perform(get("/api/open/qq-accounts").param("type", "qq").header("X-Api-Key", KEY))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(0));

        mvc.perform(delete("/api/open/qq-accounts/10001").header("X-Api-Key", KEY))
                .andExpect(status().isOk());
        mvc.perform(delete("/api/open/qq-accounts/10001").header("X-Api-Key", KEY))
                .andExpect(status().isNotFound());
    }

    @Test
    void memberBatchRegisterIdempotent() throws Exception {
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'members':[{'qq':'20001','nickname':'张三','group_id':'88888'}," +
                                "{'qq':'20002','nickname':'李四'}," +
                                "{'qq':'badqq','nickname':'非法'}]}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.added").value(2))
                .andExpect(jsonPath("$.data.skipped").value(1));

        // 幂等：再同步一次 added=0 existed=2，且昵称可覆盖
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'members':[{'qq':'20001','nickname':'张三丰','group_id':'88888'}," +
                                "{'qq':'20002','nickname':'李四'}]}")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.added").value(0))
                .andExpect(jsonPath("$.data.existed").value(2));

        String nick = jdbc.queryForObject("SELECT nickname FROM members WHERE qq='20001'", String.class);
        assertThat(nick).isEqualTo("张三丰");
    }

    @Test
    void pointsBatchQuery() throws Exception {
        // 建档两个并上分
        mvc.perform(post("/api/open/members/batch")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'members':[{'qq':'20001','nickname':'张三'},{'qq':'20002','nickname':'李四'}]}")))
                .andExpect(status().isOk());
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'qq':'20001','points':66,'reason':'测试上分'}"))).andExpect(status().isOk());

        mvc.perform(get("/api/open/points/batch").param("qqs", "20001,20003,20002")
                        .header("X-Api-Key", KEY))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(3))
                .andExpect(jsonPath("$.data.list[0].qq").value("20001"))
                .andExpect(jsonPath("$.data.list[0].points").value(66))
                .andExpect(jsonPath("$.data.list[0].exists").value(true))
                .andExpect(jsonPath("$.data.list[1].qq").value("20003"))
                .andExpect(jsonPath("$.data.list[1].exists").value(false))
                .andExpect(jsonPath("$.data.list[2].qq").value("20002"))
                .andExpect(jsonPath("$.data.list[2].exists").value(true));
    }

    @Test
    void newEndpointsRequireApiKey() throws Exception {
        mvc.perform(post("/api/open/groups")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("{'group_id':'88888'}")))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/open/members/batch")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/open/points/batch").param("qqs", "20001"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/open/qq-accounts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isUnauthorized());
    }
}
