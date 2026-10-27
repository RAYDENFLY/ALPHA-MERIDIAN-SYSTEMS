using System;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class ApiClientSecurityTests
{
    private class SecurityMockHttpMessageHandler : HttpMessageHandler
    {
        public HttpRequestMessage? LastRequest { get; private set; }
        public string ResponseJson { get; set; } = "{}";
        public HttpStatusCode ResponseStatusCode { get; set; } = HttpStatusCode.OK;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            LastRequest = request;
            var response = new HttpResponseMessage(ResponseStatusCode)
            {
                Content = new StringContent(ResponseJson, System.Text.Encoding.UTF8, "application/json")
            };
            return Task.FromResult(response);
        }
    }

    [Fact]
    public async Task CreateAlertAsync_IssuesPostToAuthorizedAlertsEndpoint()
    {
        var handler = new SecurityMockHttpMessageHandler
        {
            ResponseJson = JsonSerializer.Serialize(new AlertItemDto
            {
                AlertId = "pa_test_100",
                Symbol = "BTCUSDT",
                TargetPrice = 85000m,
                Direction = "PRICE_ABOVE",
                PriceSource = "MARK_PRICE",
                AlertType = "ONE_OFF",
                IsActive = true,
                Status = "ACTIVE"
            })
        };

        var httpClient = new HttpClient(handler);
        var client = new ApiClient(httpClient, "http://127.0.0.1:8000");

        var req = new AlertCreateRequestDto
        {
            Symbol = "BTCUSDT",
            TargetPrice = 85000m,
            Direction = "PRICE_ABOVE",
            PriceSource = "MARK_PRICE",
            AlertType = "ONE_OFF",
            Note = "Test note"
        };

        var result = await client.CreateAlertAsync(req);

        Assert.True(result.IsSuccess);
        Assert.NotNull(handler.LastRequest);
        Assert.Equal(HttpMethod.Post, handler.LastRequest.Method);
        Assert.Equal("http://127.0.0.1:8000/api/alerts", handler.LastRequest.RequestUri?.ToString());
    }

    [Fact]
    public async Task MutationSecurityBoundary_RejectsAnyNonAlertMutationEndpoint()
    {
        var handler = new SecurityMockHttpMessageHandler();
        var httpClient = new HttpClient(handler);
        var client = new ApiClient(httpClient, "http://127.0.0.1:8000");

        // Use reflection to verify internal ExecuteMutationAsync rejects non-alert routes
        var method = typeof(ApiClient).GetMethod("ExecuteMutationAsync", BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.NotNull(method);

        var genericMethod = method.MakeGenericMethod(typeof(JsonElement));

        // Attempting to invoke an order submission or session mode mutation endpoint MUST throw InvalidOperationException
        var forbiddenEndpoints = new[]
        {
            "/api/order",
            "/api/orders",
            "/api/kill-switch",
            "/api/session/mode",
            "/api/session/start",
            "/api/groq/scan",
            "/api/risk/override"
        };

        foreach (var endpoint in forbiddenEndpoints)
        {
            var task = (Task)genericMethod.Invoke(client, new object?[]
            {
                HttpMethod.Post,
                endpoint,
                null,
                CancellationToken.None
            })!;

            var ex = await Assert.ThrowsAsync<InvalidOperationException>(() => task);
            Assert.Contains("Security Boundary Violation", ex.Message);
        }
    }

    [Fact]
    public async Task UpdateAlertAsync_ValidatesTargetPricePositivity()
    {
        var handler = new SecurityMockHttpMessageHandler();
        var httpClient = new HttpClient(handler);
        var client = new ApiClient(httpClient, "http://127.0.0.1:8000");

        var res = await client.UpdateAlertAsync("pa_test", new AlertUpdateRequestDto
        {
            TargetPrice = -100m
        });

        Assert.False(res.IsSuccess);
        Assert.Contains("greater than zero", res.ErrorMessage);
        Assert.Null(handler.LastRequest); // Did not issue network request
    }
}
