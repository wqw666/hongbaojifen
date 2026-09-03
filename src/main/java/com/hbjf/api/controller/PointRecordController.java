package com.hbjf.api.controller;

import com.hbjf.api.service.MemberService;
import com.hbjf.api.util.MapBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 积分流水查询（管理员）
 */
@RestController
@RequestMapping("/api/admin/point-records")
public class PointRecordController {

    private final MemberService memberService;

    public PointRecordController(MemberService memberService) {
        this.memberService = memberService;
    }

    /** 流水：?qq=&type=INCOME/OUTCOME&page=&size= */
    @GetMapping
    public ResponseEntity<Map<String, Object>> records(@RequestParam(required = false) String qq,
                                                       @RequestParam(required = false) String type,
                                                       @RequestParam(defaultValue = "1") int page,
                                                       @RequestParam(defaultValue = "20") int size) {
        return ResponseEntity.ok(MapBuilder.of("code", 0, "data", memberService.records(qq, type, page, size)));
    }
}
