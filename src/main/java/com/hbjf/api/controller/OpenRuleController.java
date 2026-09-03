package com.hbjf.api.controller;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.service.PlayRuleService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.File;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * 玩法开放接口（agent 调用，/api/open/** 下，需 X-Api-Key）
 * GET /api/open/rules                 — 启用的玩法列表
 * GET /api/open/rules/{id}/download   — 下载玩法规则文件
 */
@RestController
@RequestMapping("/api/open/rules")
public class OpenRuleController {

    private final PlayRuleService playRuleService;

    public OpenRuleController(PlayRuleService playRuleService) {
        this.playRuleService = playRuleService;
    }

    /** 启用的玩法列表 */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list() {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", playRuleService.listActiveForAgent()));
    }

    /** 下载玩法文件 */
    @GetMapping("/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable Long id) {
        Map<String, Object> row = playRuleService.findById(id);
        if (row == null) throw new ApiException(ErrorCode.NOT_FOUND, "玩法不存在");
        if (!"active".equals(row.get("status"))) throw new ApiException(ErrorCode.PARAM_INVALID, "玩法已停用");
        File f = playRuleService.getFile(row);
        String encoded = URLEncoder.encode(String.valueOf(row.get("name")), StandardCharsets.UTF_8).replace("+", "%20");
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .body(new FileSystemResource(f));
    }
}
