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

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 用户管理测试：新增/删除/重置密码/改自己密码 + 内置 admin 保护 + 仅 admin 可访问
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AdminUserFlowTest {

    @Autowired
    MockMvc mvc;
    @Autowired
    ObjectMapper om;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeEach
    void cleanUsers() {
        // 保留内置 admin 种子，清掉测试期创建的其它用户
        jdbc.update("DELETE FROM admin_users WHERE username <> 'admin'");
    }

    private String loginToken(String username, String password) throws Exception {
        MvcResult res = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"" + username + "\",\"password\":\"" + password + "\"}"))
                .andExpect(status().isOk())
                .andReturn();
        return om.readTree(res.getResponse().getContentAsString()).get("data").get("token").asText();
    }

    @Test
    void adminCanCreateUserAndNewUserCanLogin() throws Exception {
        String adminToken = loginToken("admin", "admin123");

        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op1\",\"nickname\":\"操作员一\",\"password\":\"op123456\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));

        // 新用户可登录
        loginToken("op1", "op123456");

        // 列表包含新用户且不返回密码哈希、内置 admin 带 builtin 标记
        mvc.perform(get("/api/admin/users").header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[?(@.username=='admin')].builtin").value(true))
                .andExpect(jsonPath("$.data[?(@.username=='op1')].builtin").value(false))
                .andExpect(jsonPath("$.data[0].password_hash").doesNotExist());
    }

    @Test
    void createWithDuplicateUsernameFails() throws Exception {
        String adminToken = loginToken("admin", "admin123");

        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op2\",\"password\":\"op123456\"}"))
                .andExpect(status().isOk());

        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op2\",\"password\":\"other123\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("已存在")));
    }

    @Test
    void weakPasswordRejected() throws Exception {
        String adminToken = loginToken("admin", "admin123");
        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op3\",\"password\":\"123\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void nonAdminCannotManageUsers() throws Exception {
        String adminToken = loginToken("admin", "admin123");
        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op4\",\"nickname\":\"\",\"password\":\"op123456\"}"))
                .andExpect(status().isOk());
        String opToken = loginToken("op4", "op123456");

        mvc.perform(get("/api/admin/users").header("Authorization", "Bearer " + opToken))
                .andExpect(status().isForbidden());
        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + opToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"evil\",\"password\":\"op123456\"}"))
                .andExpect(status().isForbidden());
        mvc.perform(delete("/api/admin/users/1").header("Authorization", "Bearer " + opToken))
                .andExpect(status().isForbidden());
    }

    @Test
    void builtinAdminCannotBeDeletedButPasswordResettable() throws Exception {
        String adminToken = loginToken("admin", "admin123");

        Long adminId = jdbc.queryForObject("SELECT id FROM admin_users WHERE username='admin'", Long.class);
        mvc.perform(delete("/api/admin/users/" + adminId).header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isBadRequest());

        // admin 重置自己密码后旧密码失效、新密码可登录
        mvc.perform(put("/api/admin/users/" + adminId + "/password").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"password\":\"newAdmin456\"}"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"admin\",\"password\":\"admin123\"}"))
                .andExpect(status().isUnauthorized());
        loginToken("admin", "newAdmin456");

        // 恢复默认密码，避免影响其它用例
        mvc.perform(put("/api/admin/users/" + adminId + "/password").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"password\":\"admin123\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void adminCanDeleteNormalUserAndResetTheirPassword() throws Exception {
        String adminToken = loginToken("admin", "admin123");
        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op5\",\"password\":\"op123456\"}"))
                .andExpect(status().isOk());
        Long opId = jdbc.queryForObject("SELECT id FROM admin_users WHERE username='op5'", Long.class);

        // 重置他人密码后新密码可登录
        mvc.perform(put("/api/admin/users/" + opId + "/password").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"password\":\"reset456\"}"))
                .andExpect(status().isOk());
        loginToken("op5", "reset456");

        mvc.perform(delete("/api/admin/users/" + opId).header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isOk());
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op5\",\"password\":\"reset456\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void changeOwnPasswordRequiresOldPassword() throws Exception {
        String adminToken = loginToken("admin", "admin123");
        mvc.perform(post("/api/admin/users").header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op6\",\"password\":\"op123456\"}"))
                .andExpect(status().isOk());
        String opToken = loginToken("op6", "op123456");

        // 旧密码错误 → 拒绝
        mvc.perform(put("/api/auth/password").header("Authorization", "Bearer " + opToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"old_password\":\"wrong\",\"new_password\":\"new456789\"}"))
                .andExpect(status().isUnauthorized());

        // 旧密码正确 → 生效，且新 token（旧会话）仍由登录校验把关
        mvc.perform(put("/api/auth/password").header("Authorization", "Bearer " + opToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"old_password\":\"op123456\",\"new_password\":\"new456789\"}"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"op6\",\"password\":\"op123456\"}"))
                .andExpect(status().isUnauthorized());
        loginToken("op6", "new456789");
    }

    @Test
    void cannotDeleteLastRemainingAdminAccount() throws Exception {
        // 只有内置 admin 一个账号时也不可删（builtin 保护已覆盖）；多用户场景下任意普通账号删除不影响 admin
        String adminToken = loginToken("admin", "admin123");
        Long adminId = jdbc.queryForObject("SELECT id FROM admin_users WHERE username='admin'", Long.class);
        mvc.perform(delete("/api/admin/users/" + adminId).header("Authorization", "Bearer " + adminToken))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("内置")));
    }
}
