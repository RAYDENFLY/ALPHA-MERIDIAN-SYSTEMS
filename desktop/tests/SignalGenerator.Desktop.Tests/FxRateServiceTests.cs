using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class FxRateServiceTests
{
    private class MockHttpMessageHandler : HttpMessageHandler
    {
        public int CallCount { get; private set; }
        private readonly string _responseJson;
        private readonly HttpStatusCode _statusCode;

        public MockHttpMessageHandler(string responseJson, HttpStatusCode statusCode = HttpStatusCode.OK)
        {
            _responseJson = responseJson;
            _statusCode = statusCode;
        }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            CallCount++;
            var response = new HttpResponseMessage(_statusCode)
            {
                Content = new StringContent(_responseJson, Encoding.UTF8, "application/json")
            };
            return Task.FromResult(response);
        }
    }

    [Fact]
    public async Task GetLatestRateAsync_ParsesFrankfurterResponse_Accurately()
    {
        string json = @"{
            ""amount"": 1.0,
            ""base"": ""USD"",
            ""date"": ""2026-10-27"",
            ""rates"": { ""IDR"": 18139.0 }
        }";

        var handler = new MockHttpMessageHandler(json);
        var client = new HttpClient(handler);
        var service = new FxRateService(client);

        var result = await service.GetLatestRateAsync();

        Assert.Equal(18139m, result.Rate);
        Assert.False(result.IsStale);
        Assert.Contains("European Central Bank", result.Source);
        Assert.Null(result.ErrorMessage);
    }

    [Fact]
    public async Task GetHistoricalSeriesAsync_CachesResult_AndPreventsDuplicateNetworkRequests()
    {
        string json = @"{
            ""amount"": 1.0,
            ""base"": ""USD"",
            ""start_date"": ""2026-09-27"",
            ""end_date"": ""2026-10-27"",
            ""rates"": {
                ""2026-09-27"": { ""IDR"": 18000.0 },
                ""2026-10-15"": { ""IDR"": 18100.0 },
                ""2026-10-27"": { ""IDR"": 18139.0 }
            }
        }";

        var handler = new MockHttpMessageHandler(json);
        var client = new HttpClient(handler);
        var service = new FxRateService(client);

        // First call - should trigger HTTP request
        var series1 = await service.GetHistoricalSeriesAsync("1M");
        Assert.Equal(1, handler.CallCount);
        Assert.Equal(18139m, series1.CurrentRate);
        Assert.Equal(18000m, series1.StartRate);
        Assert.Equal(0.77m, series1.ChangePct); // (18139 - 18000)/18000 * 100 = 0.77%
        Assert.Equal(18000m, series1.MinRate);
        Assert.Equal(18139m, series1.MaxRate);
        Assert.Equal(3, series1.Points.Count);

        // Second call within TTL - must return cached instance with ZERO additional HTTP calls
        var series2 = await service.GetHistoricalSeriesAsync("1M");
        Assert.Equal(1, handler.CallCount); // Still 1 call!
        Assert.Same(series1, series2);
    }

    [Fact]
    public async Task GetHistoricalSeriesAsync_HandlesNetworkFailure_WithGracefulFallback()
    {
        var handler = new MockHttpMessageHandler("", HttpStatusCode.InternalServerError);
        var client = new HttpClient(handler);
        var service = new FxRateService(client);

        var series = await service.GetHistoricalSeriesAsync("1W");

        // Should return a fallback series without throwing unhandled exception
        Assert.NotNull(series);
        Assert.True(series.IsStale);
        Assert.NotNull(series.ErrorMessage);
        Assert.True(series.CurrentRate > 0);
        Assert.NotEmpty(series.Points);
    }
}
