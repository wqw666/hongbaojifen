package com.hbjf.api.controller;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 首页入口 + 健康检查
 */
@Controller
public class IndexController {

    @GetMapping("/health")
    @ResponseBody
    public Map<String, Object> health() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "ok");
        result.put("time", OffsetDateTime.now(ZoneOffset.UTC).toString());
        return result;
    }

    /** 根路径 → 重定向到管理后台 SPA */
    @GetMapping({"/", "/index.html"})
    public String rootIndex() {
        return "redirect:/admin/";
    }

    /** PC 管理后台入口 → React SPA */
    @GetMapping({"/admin", "/admin/"})
    public String adminIndex() {
        return "forward:/admin/index.html";
    }

    /** SPA 深层路由刷新 fallback：/admin/xxx（非静态文件）→ index.html */
    @GetMapping("/admin/{path:[^.]*}")
    public String spaFallback(HttpServletRequest request) {
        return "forward:/admin/index.html";
    }
}
