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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 开放接口（/api/open/**）测试：X-Api-Key 校验 + 上分/下分/查询/流水
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class OpenApiTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeEach
    void clean() {
        jdbc.update("TRUNCATE TABLE point_records");
        jdbc.update("TRUNCATE TABLE members");
    }

    @Test
    void requiresApiKey() throws Exception {
        // 无 key → 401
        mvc.perform(post("/api/open/points/up")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20001\",\"points\":10,\"reason\":\"test\"}"))
                .andExpect(status().isUnauthorized());
        // 错误 key → 401
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "wrong-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20001\",\"points\":10,\"reason\":\"test\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void upAutoCreatesMember() throws Exception {
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20001\",\"points\":66,\"reason\":\"游戏胜利奖励\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(66))
                .andExpect(jsonPath("$.data.qq").value("20001"));

        Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM members WHERE qq='20001'", Integer.class);
        assertThat(c).isEqualTo(1); // 自动建档
    }

    @Test
    void upRequiresPositivePoints() throws Exception {
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20002\",\"points\":0,\"reason\":\"test\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void downNotEnoughRejected() throws Exception {
        mvc.perform(post("/api/open/points/down")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20003\",\"points\":10,\"reason\":\"兑换\"}"))
                .andExpect(status().isNotFound()) // 会员不存在无法下分
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("未建档")));

        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20003\",\"points\":10,\"reason\":\"签到\"}"))
                .andExpect(status().isOk());

        // 下分 20 > 余额 10 → 409
        mvc.perform(post("/api/open/points/down")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20003\",\"points\":20,\"reason\":\"兑换\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("积分不足")));
    }

    @Test
    void queryAndRecords() throws Exception {
        mvc.perform(post("/api/open/points/up")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20004\",\"points\":30,\"reason\":\"签到\"}"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/open/points/down")
                        .header("X-Api-Key", "test-open-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"qq\":\"20004\",\"points\":12,\"reason\":\"红包兑换\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(18));

        // 查积分
        mvc.perform(get("/api/open/points/20004").header("X-Api-Key", "test-open-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(18))
                .andExpect(jsonPath("$.data.total_income").value(30))
                .andExpect(jsonPath("$.data.total_outcome").value(12));

        // 不存在的会员 → exists=false 积分 0
        mvc.perform(get("/api/open/points/99999").header("X-Api-Key", "test-open-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.exists").value(false))
                .andExpect(jsonPath("$.data.points").value(0));

        // 查流水
        mvc.perform(get("/api/open/points/records").param("qq", "20004").header("X-Api-Key", "test-open-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(2));
    }
}
