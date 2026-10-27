using System;

namespace SignalGenerator.Desktop.Models;

/// <summary>
/// Generic result envelope for API calls with success/error status.
/// </summary>
public record ApiResult<T>
{
    public bool IsSuccess { get; init; }
    public T? Value { get; init; }
    public string? ErrorMessage { get; init; }
    public int? StatusCode { get; init; }
    public DateTime Timestamp { get; init; } = DateTime.UtcNow;

    public static ApiResult<T> Success(T value, int statusCode = 200) => new()
    {
        IsSuccess = true,
        Value = value,
        StatusCode = statusCode,
        ErrorMessage = null
    };

    public static ApiResult<T> Failure(string errorMessage, int? statusCode = null) => new()
    {
        IsSuccess = false,
        Value = default,
        StatusCode = statusCode,
        ErrorMessage = errorMessage
    };
}
