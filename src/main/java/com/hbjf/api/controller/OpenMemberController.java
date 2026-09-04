package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.service.OpenMemberService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 会员开放接口（agent 同步群成员到会员库；调用方需带 X-Api-Key）
 *   POST /api/open/members/batch
 *     {members:[{qq, nickname?, group_id?}], executor_token?}  批量注册（幂等，≤1000）
 *     executor_token 存在时：校验执行器未被封禁、操作员正常，并将该管理员QQ记为建档注册人 registrar_qq
 * 批量查积分见 GET /api/open/points/batch?qqs= （OpenPointController）
 */
@RestController
@RequestMapping("/api/open/members")
public class OpenMemberController {

    private final OpenMemberService openMemberService;
    private final ExecutorService executorService;

    public OpenMemberController(OpenMemberService openMemberService, ExecutorService executorService) {
        this.openMemberService = openMemberService;
        this.executorService = executorService;
    }

    /** 批量注册会员（已存在自动跳过并可选覆盖昵称/群号） */
    @PostMapping("/batch")
    public ResponseEntity<Map<String, Object>> registerBatch(@RequestBody Map<String, Object> body) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> members = (List<Map<String, Object>>) body.get("members");
        String token = body.get("executor_token") == null ? "" : String.valueOf(body.get("executor_token"));
        String registrarQq = "";
        if (!token.isEmpty()) {
            registrarQq = String.valueOf(executorService.requireOperable(token, "write").get("admin_qq"));
        }
        Map<String, Object> result = openMemberService.registerBatch(members, registrarQq);
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message",
                "新增 " + result.get("added") + "，已存在 " + result.get("existed") + "，跳过 " + result.get("skipped"), "data", result));
    }
}
