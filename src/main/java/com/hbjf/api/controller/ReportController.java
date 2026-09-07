package com.hbjf.api.controller;

import com.hbjf.api.service.ReportService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 报表展示（管理员只读统计总览，供非技术人员查看）
 */
@RestController
@RequestMapping("/api/admin/report")
public class ReportController {

    private final ReportService reportService;

    public ReportController(ReportService reportService) {
        this.reportService = reportService;
    }

    /** 报表总览：总览/今日/近7日趋势/近N天/执行器群明细/排行 一次返回 */
    @GetMapping("/overview")
    public ResponseEntity<Map<String, Object>> overview() {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "message", "ok", "data", reportService.overview()));
    }
}
