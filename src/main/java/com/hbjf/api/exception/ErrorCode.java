package com.hbjf.api.exception;

/**
 * API 错误码
 */
public enum ErrorCode {

    SUCCESS(0, "成功"),
    PARAM_INVALID(40001, "参数校验失败"),
    NOT_LOGGED_IN(40100, "未登录"),
    TOKEN_EXPIRED(40101, "Token 已过期"),
    WRONG_CREDENTIALS(40102, "用户名或密码错误"),
    PERMISSION_DENIED(40300, "权限不足"),
    NOT_FOUND(40400, "资源不存在"),
    BALANCE_NOT_ENOUGH(40900, "积分不足"),
    DUPLICATE_BIZ(40901, "重复的业务单号"),
    INTERNAL_ERROR(50002, "服务内部错误");

    private final int code;
    private final String defaultMessage;

    ErrorCode(int code, String defaultMessage) {
        this.code = code;
        this.defaultMessage = defaultMessage;
    }

    public int getCode() { return code; }
    public String getDefaultMessage() { return defaultMessage; }
}
