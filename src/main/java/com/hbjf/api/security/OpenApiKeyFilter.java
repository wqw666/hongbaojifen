package com.hbjf.api.security;

import com.hbjf.api.config.AppProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * 开放接口密钥过滤器：/api/open/** 必须带 X-Api-Key 请求头
 * （agent 部署端与系统共享密钥；若不需要可把 app.open.api-key 配为空字符串）
 */
@Component
public class OpenApiKeyFilter extends OncePerRequestFilter {

    private final AppProperties props;

    public OpenApiKeyFilter(AppProperties props) {
        this.props = props;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/api/open/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String expected = props.getOpen().getApiKey();
        if (expected == null || expected.isEmpty()) {
            // 未配置密钥 = 关闭校验（内网使用）
            filterChain.doFilter(request, response);
            return;
        }
        String actual = request.getHeader("X-Api-Key");
        if (actual == null || !expected.equals(actual)) {
            response.setStatus(401);
            response.setContentType("application/json; charset=UTF-8");
            response.getWriter().write("{\"code\":40101,\"message\":\"开放接口密钥无效\"}");
            return;
        }
        filterChain.doFilter(request, response);
    }
}
