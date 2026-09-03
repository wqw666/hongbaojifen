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

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 会员积分核心流程测试：登录 → 上分自动建档 → 下分 → 余额不足拒绝 → 幂等
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class PointFlowTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    ObjectMapper om;

    String adminToken;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
        MvcResult login = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk())
                .andReturn();
        adminToken = om.readTree(login.getResponse().getContentAsString()).get("data").get("token").asText();
    }

    private String auth() {
        return "Bearer " + adminToken;
    }

    @Test
    void upPointsAutoCreatesMember() throws Exception {
        mvc.perform(post("/api/admin/members/1/points")
                        .header("Authorization", auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delta\":100,\"reason\":\"签到奖励\"}"))
                .andExpect(status().isNotFound()); // id=1 不存在

        // 先建档再上分
        mvc.perform(post("/api/admin/members")
                        .header("Authorization", auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"10001\",\"nickname\":\"测试会员\",\"group_id\":\"88888\"}"))
                .andExpect(status().isOk());

        MvcResult res = mvc.perform(post("/api/admin/members/1/points")
                        .header("Authorization", auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delta\":100,\"reason\":\"签到奖励\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(100))
                .andReturn();
        System.out.println("up result: " + res.getResponse().getContentAsString());

        Integer points = jdbc.queryForObject("SELECT points FROM members WHERE qq='10001'", Integer.class);
        assertThat(points).isEqualTo(100);
        Integer records = jdbc.queryForObject("SELECT COUNT(*) FROM point_records WHERE qq='10001' AND type='INCOME'", Integer.class);
        assertThat(records).isEqualTo(1);
    }

    @Test
    void downPointsBalanceNotEnough() throws Exception {
        jdbc.update("INSERT INTO members (qq, nickname, points, created_at, updated_at) VALUES ('10002','张三',50,'','')");
        // 下分 60 > 余额 50 → 409
        mvc.perform(post("/api/admin/members/1/points")
                        .header("Authorization", auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delta\":-60,\"reason\":\"兑换\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("积分不足")));
        // 余额不变
        Integer points = jdbc.queryForObject("SELECT points FROM members WHERE qq='10002'", Integer.class);
        assertThat(points).isEqualTo(50);
    }

    @Test
    void downPointsSuccess() throws Exception {
        jdbc.update("INSERT INTO members (qq, nickname, points, created_at, updated_at) VALUES ('10003','李四',200,'','')");
        mvc.perform(post("/api/admin/members/1/points")
                        .header("Authorization", auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delta\":-80,\"reason\":\"红包兑换\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(120));
        Integer totalOutcome = jdbc.queryForObject("SELECT total_outcome FROM members WHERE qq='10003'", Integer.class);
        assertThat(totalOutcome).isEqualTo(80);
    }

    @Test
    void duplicateBizNoIsIdempotent() throws Exception {
        // 通过开放接口验证幂等（bizNo 相同只加减一次）
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"10004\",\"points\":50,\"reason\":\"游戏奖励\",\"bizNo\":\"GAME-2026-001\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(50));

        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"10004\",\"points\":50,\"reason\":\"游戏奖励\",\"bizNo\":\"GAME-2026-001\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.duplicate").value(true));

        Integer points = jdbc.queryForObject("SELECT points FROM members WHERE qq='10004'", Integer.class);
        assertThat(points).isEqualTo(50); // 只加了一次
    }

    @Test
    void statsAndRecords() throws Exception {
        jdbc.update("INSERT INTO members (qq, nickname, points, total_income, total_outcome, created_at, updated_at) VALUES ('10005','王五',10,10,0,'','')");
        jdbc.update("INSERT INTO point_records (qq, member_id, delta, type, reason, operator, biz_no, created_at) VALUES ('10005',1,10,'INCOME','签到','admin','','2026-08-01 10:00:00')");

        mvc.perform(get("/api/admin/members/stats").header("Authorization", auth()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.member_count").value(1))
                .andExpect(jsonPath("$.data.total_points").value(10));

        mvc.perform(get("/api/admin/point-records").param("qq", "10005").header("Authorization", auth()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.list[0].delta").value(10));
    }
}
