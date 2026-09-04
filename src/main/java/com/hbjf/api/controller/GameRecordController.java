package com.hbjf.api.controller;

import com.hbjf.api.service.GameRecordService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 游戏记录管理（总后台，JWT）
 *   GET /api/admin/game-records           分页列表（?group_id=&play_name=&page=&size=）
 *   GET /api/admin/game-records/{id}      局详情（record + events 时间线，供回放）
 */
@RestController
@RequestMapping("/api/admin/game-records")
public class GameRecordController {

    private final GameRecordService gameRecordService;

    public GameRecordController(GameRecordService gameRecordService) {
        this.gameRecordService = gameRecordService;
    }

    /** 游戏记录分页列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(value = "group_id", required = false) String groupId,
                                                    @RequestParam(value = "play_name", required = false) String playName,
                                                    @RequestParam(defaultValue = "1") int page,
                                                    @RequestParam(defaultValue = "20") int size) {
        int p = Math.max(1, page);
        int s = Math.min(100, Math.max(1, size));
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", gameRecordService.list(groupId, playName, p, s)));
    }

    /** 局详情（含事件时间线，回放用） */
    @GetMapping("/{id}")
    public ResponseEntity<Map<String, Object>> detail(@PathVariable Long id) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", gameRecordService.detail(id)));
    }
}
