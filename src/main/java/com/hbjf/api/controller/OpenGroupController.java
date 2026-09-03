package com.hbjf.api.controller;

import com.hbjf.api.service.OpenGroupService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * QQ群开放接口（agent 上报群信息；调用方需带 X-Api-Key）
 *   POST /api/open/groups  {group_id, group_name?, owner_qq?, admin_qqs?, member_count?, note?}  新增或更新（幂等）
 *   GET  /api/open/groups  ?keyword=&status=                                                      列表
 */
@RestController
@RequestMapping("/api/open/groups")
public class OpenGroupController {

    private final OpenGroupService openGroupService;

    public OpenGroupController(OpenGroupService openGroupService) {
        this.openGroupService = openGroupService;
    }

    /** 上报/更新群信息 */
    @PostMapping
    public ResponseEntity<Map<String, Object>> upsert(@RequestBody Map<String, String> body) {
        Map<String, Object> result = openGroupService.upsert(
                body.get("group_id"), body.get("group_name"), body.get("owner_qq"),
                body.get("admin_qqs"), body.get("member_count"), body.get("note"));
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
