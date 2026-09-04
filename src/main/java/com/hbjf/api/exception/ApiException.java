package com.hbjf.api.exception;

import org.springframework.http.HttpStatus;

/**
 * 业务异常，携带错误码和 HTTP 状态码
 */
public class ApiException extends RuntimeException {

    private final ErrorCode errorCode;
    private final HttpStatus httpStatus;

    public ApiException(ErrorCode errorCode) {
        super(errorCode.getDefaultMessage());
        this.errorCode = errorCode;
        this.httpStatus = mapStatus(errorCode);
    }

    public ApiException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
        this.httpStatus = mapStatus(errorCode);
    }

    public ErrorCode getErrorCode() { return errorCode; }
    public HttpStatus getHttpStatus() { return httpStatus; }

    private static HttpStatus mapStatus(ErrorCode code) {
        switch (code) {
            case PARAM_INVALID:
                return HttpStatus.BAD_REQUEST;
            case NOT_LOGGED_IN:
            case TOKEN_EXPIRED:
            case WRONG_CREDENTIALS:
                return HttpStatus.UNAUTHORIZED;
            case PERMISSION_DENIED:
            case EXECUTOR_BANNED:
            case OPERATOR_DISABLED:
                return HttpStatus.FORBIDDEN;
            case NOT_FOUND:
                return HttpStatus.NOT_FOUND;
            case BALANCE_NOT_ENOUGH:
            case DUPLICATE_BIZ:
                return HttpStatus.CONFLICT;
            default:
                return HttpStatus.INTERNAL_SERVER_ERROR;
        }
    }
}
