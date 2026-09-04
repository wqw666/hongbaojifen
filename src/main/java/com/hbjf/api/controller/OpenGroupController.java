package com.hbjf.api.controller;

import com.hbjf.api.service.ExecutorService;
import com.hbjf.api.service.OpenGroupService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * QQ群开放接口（agent 上报群信息；调用方需带 X-Api-Key）
 *   POST /api/open/groups
 *     {group_id, group_name?, owner_qq?, admin_qqs?, member_count?, create_time?, note?, executor_token?}
 *     create_time=QQ 群创建时间；executor_token 用于封禁守卫与绑定管理执行器（建议必带，缺省跳过守卫=旧行为）
 *   GET  /api/open/groups  ?keyword=&status=                      列表（含 status：agent 依此停玩被封群）
 */
@RestController
@RequestMapping("/api/open/groups")
public class OpenGroupController {

    private final OpenGroupService openGroupService;
    private final ExecutorService executorService;

    public OpenGroupController(OpenGroupService openGroupService, ExecutorService executorService) {
        this.openGroupService = openGroupService;
        this.executorService = executorService;
    }

    /** 上报/更新群信息 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> upsert(@RequestBody Map<String, String> body) {
        // 执行器身份：带 token 则走封禁守卫并绑定管理执行器；不带则兼容旧调用（仅 api-key）
        Long executorId = 0L;
        String token = body.get("executor_token");
        if (token != null && !token.isEmpty()) {
            executorId = (Long) executorService.requireOperable(token, "write").get("id");
        }
        Map<String, Object> result = openGroupService.upsert(
                body.get("group_id"), body.get("group_name"), body.get("owner_qq"),
                body.get("admin_qqs"), body.get("member_count"), body.get("note"),
                executorId, body.get("create_time"));
        boolean created = Boolean.TRUE.equals(result.get("created"));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", created ? "群信息已新增" : "群信息已更新", "data", result));
    }

    /** 群列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword,
                                                    @RequestParam(required = false) String status) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", openGroupService.list(keyword, status)));
    }
}
