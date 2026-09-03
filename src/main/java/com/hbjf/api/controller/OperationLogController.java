package com.hbjf.api.controller;

import com.hbjf.api.service.OperationLogService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 页面操作记录查询（管理员）
 */
@RestController
@RequestMapping("/api/admin/logs")
public class OperationLogController {

    private final OperationLogService operationLogService;

    public OperationLogController(OperationLogService operationLogService) {
        this.operationLogService = operationLogService;
    }

    /** 列表：?keyword=&limit= */
    @GetMapping
    public ResponseEntity<Map<String, Object>> list(@RequestParam(required = false) String keyword,
                                                    @RequestParam(defaultValue = "200") int limit) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", operationLogService.list(keyword, limit)));
    }
}
