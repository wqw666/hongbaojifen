package com.hbjf.api.config;

import com.hbjf.api.exception.ApiException;
import com.hbjf.api.exception.ErrorCode;
import com.hbjf.api.util.MapBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

import java.util.Map;

/**
 * 全局异常处理：统一返回 {code, message} 结构
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<Map<String, Object>> handleApi(ApiException e) {
        return ResponseEntity.status(e.getHttpStatus())
                .body(MapBuilder.of("code", e.getErrorCode().getCode(), "message", e.getMessage()));
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Map<String, Object>> handleUploadSize(MaxUploadSizeExceededException e) {
        return ResponseEntity.badRequest()
                .body(MapBuilder.of("code", ErrorCode.PARAM_INVALID.getCode(), "message", "文件超过大小限制（最大 20MB）"));
    }

    @ExceptionHandler(DuplicateKeyException.class)
    public ResponseEntity<Map<String, Object>> handleDuplicate(DuplicateKeyException e) {
        return ResponseEntity.status(409)
                .body(MapBuilder.of("code", ErrorCode.PARAM_INVALID.getCode(), "message", "数据已存在（唯一约束冲突）"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleOther(Exception e) {
        log.error("Unhandled exception", e);
        return ResponseEntity.status(500)
                .body(MapBuilder.of("code", ErrorCode.INTERNAL_ERROR.getCode(), "message", "服务内部错误"));
    }
}
