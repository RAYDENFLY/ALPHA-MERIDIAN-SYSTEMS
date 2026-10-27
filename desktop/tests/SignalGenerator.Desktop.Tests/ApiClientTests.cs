using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class ApiClientTests
{
    private class MockHttpMessageHandler : HttpMessageHandler
    {
        private readonly HttpResponseMessage _response;

        public MockHttpMessageHandler(HttpResponseMessage response)
        {
            _response = response;
        }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(_response);
        }
    }

    [Fact]
    public async Task GetStatusAsync_ReturnsSuccess_WhenBackendResponds200()
    {
        const string json = """
        {
          "app_name": "SMC-SNR ML Perpetual Scanner",
          "version": "1.0.0",
          "mode": "LIVE_AUTO",
          "base_mode": "PAPER",
          "session_state": "ACTIVE",
          "session_id": "SESS_123",
          "live_trading_locked": true
        }
        """;

        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };

        var client = new ApiClient(new HttpClient(new MockHttpMessageHandler(response)), "http://127.0.0.1:8000");
        var result = await client.GetStatusAsync();

        Assert.True(result.IsSuccess);
        Assert.NotNull(result.Value);
        Assert.Equal("LIVE_AUTO", result.Value.Mode);
        Assert.Equal("ACTIVE", result.Value.SessionState);
        Assert.Equal("SESS_123", result.Value.SessionId);
    }

    [Fact]
    public async Task GetStatusAsync_ReturnsFailure_WhenBackendResponds500()
    {
        var response = new HttpResponseMessage(HttpStatusCode.InternalServerError)
        {
            Content = new StringContent("Internal server panic", Encoding.UTF8, "text/plain")
        };

        var client = new ApiClient(new HttpClient(new MockHttpMessageHandler(response)), "http://127.0.0.1:8000");
        var result = await client.GetStatusAsync();

        Assert.False(result.IsSuccess);
        Assert.Null(result.Value);
        Assert.Contains("500", result.ErrorMessage);
    }

    [Fact]
    public async Task GetLiveSessionAsync_ReturnsSuccess_WhenBackendResponds200()
    {
        const string json = """
        {
          "status": "SUCCESS",
          "data": {
            "session_id": "SESS_TEST",
            "mode": "LIVE_AUTO",
            "state": "ACTIVE",
            "daily_filled_entries": 1,
            "entry_limit": 3
          }
        }
        """;

        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };

        var client = new ApiClient(new HttpClient(new MockHttpMessageHandler(response)), "http://127.0.0.1:8000");
        var result = await client.GetLiveSessionAsync();

        Assert.True(result.IsSuccess);
        Assert.NotNull(result.Value);
        Assert.NotNull(result.Value.Data);
        Assert.Equal(1, result.Value.Data.DailyFilledEntries);
        Assert.Equal(3, result.Value.Data.EntryLimit);
    }

    [Fact]
    public async Task GetJournalSummaryAsync_ReturnsSuccess_WhenBackendResponds200()
    {
        const string json = """
        {
          "mode": "LIVE",
          "total_closed_trades": 5,
          "win_rate_pct": 60.0,
          "reconciliation_ok": true
        }
        """;

        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };

        var client = new ApiClient(new HttpClient(new MockHttpMessageHandler(response)), "http://127.0.0.1:8000");
        var result = await client.GetJournalSummaryAsync();

        Assert.True(result.IsSuccess);
        Assert.NotNull(result.Value);
        Assert.Equal(5, result.Value.TotalClosedTrades);
        Assert.Equal(60.0m, result.Value.WinRatePct);
        Assert.True(result.Value.ReconciliationOk);
    }
}
