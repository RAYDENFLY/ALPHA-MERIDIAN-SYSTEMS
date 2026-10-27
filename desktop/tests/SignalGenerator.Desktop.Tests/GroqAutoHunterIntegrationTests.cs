using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Serialization;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class GroqAutoHunterIntegrationTests
{
    private class MockHttpMessageHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, HttpResponseMessage> _handler;

        public MockHttpMessageHandler(Func<HttpRequestMessage, HttpResponseMessage> handler)
        {
            _handler = handler;
        }

        public MockHttpMessageHandler(HttpResponseMessage response)
        {
            _handler = _ => response;
        }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(_handler(request));
        }
    }

    [Fact]
    public void JsonRecursionSanitizer_ReproducesAndFixes_MaxDepthExceededFailure()
    {
        // 1. Construct JSON with risk_gate_status nested 75 levels deep (exceeding default MaxDepth 64 and bounded MaxDepth 32)
        var sb = new StringBuilder();
        sb.Append("{\"status\":\"SUCCESS\",\"latest\":{\"cycle_id\":\"cycle-test-deep\",\"stage1\":{\"total_screened_pairs\":10,\"top_10\":[]},\"risk_gate_status\":");
        for (int i = 0; i < 75; i++)
        {
            sb.Append("{\"nested\":");
        }
        sb.Append("\"deep_value\"");
        for (int i = 0; i < 75; i++)
        {
            sb.Append('}');
        }
        sb.Append("}}");

        string deepJson = sb.ToString();

        // 2. Verify that raw deserialization with MaxDepth=32 or MaxDepth=64 throws JsonException
        var strictOptions = new JsonSerializerOptions { MaxDepth = 32 };
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<AutotraderScreeningLogResponseDto>(deepJson, strictOptions));

        // 3. Sanitize using JsonRecursionSanitizer
        string sanitizedJson = JsonRecursionSanitizer.SanitizeString(deepJson);

        // 4. Verify sanitized JSON parses cleanly within bounded MaxDepth=32
        var parsed = JsonSerializer.Deserialize<AutotraderScreeningLogResponseDto>(sanitizedJson, strictOptions);
        Assert.NotNull(parsed);
        Assert.Equal("SUCCESS", parsed.Status);
        Assert.NotNull(parsed.Latest);
        Assert.Equal("cycle-test-deep", parsed.Latest.CycleId);
        Assert.Equal(10, parsed.Latest.Stage1?.TotalScreenedPairs);
    }

    [Fact]
    public async Task ApiClient_GetScreeningLogAsync_ParsesPayloadWithRecursiveRiskGateStatus()
    {
        var sb = new StringBuilder();
        sb.Append("{\"status\":\"SUCCESS\",\"active_cycle\":{\"cycle_id\":\"cycle-active-99\"},\"latest\":{\"cycle_id\":\"cycle-latest-99\",\"timestamp_utc\":\"2026-10-27T21:30:00Z\",\"status\":\"REJECTED\",\"stage1\":{\"total_screened_pairs\":638,\"top_10\":[{\"symbol\":\"ETHUSDT\",\"mark_price\":2341.42,\"trend_4h\":\"BULLISH\"}]},\"stage2\":{\"top_3\":[{\"rank\":1,\"symbol\":\"ETHUSDT\",\"score\":82.5,\"thesis\":\"High relative strength\"}]},\"stage3\":{\"decision\":\"WAIT\",\"symbol\":\"ETHUSDT\",\"reasoning\":\"Equilibrium zone lock\"},\"risk_gate_status\":");
        for (int i = 0; i < 80; i++) sb.Append("{\"cycle\":");
        sb.Append("{\"state\":\"RECURSIVE\"}");
        for (int i = 0; i < 80; i++) sb.Append('}');
        sb.Append("},\"history\":[]}");

        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(sb.ToString(), Encoding.UTF8, "application/json")
        };

        var client = new ApiClient(new HttpClient(new MockHttpMessageHandler(response)), "http://127.0.0.1:8000");
        var result = await client.GetScreeningLogAsync();

        Assert.True(result.IsSuccess);
        Assert.NotNull(result.Value);
        Assert.Equal("SUCCESS", result.Value.Status);
        Assert.NotNull(result.Value.Latest);
        Assert.Equal("cycle-latest-99", result.Value.Latest.CycleId);
        Assert.Single(result.Value.Latest.Stage1!.Top10);
        Assert.Equal("ETHUSDT", result.Value.Latest.Stage1.Top10[0].Symbol);
        Assert.Equal("BULLISH", result.Value.Latest.Stage1.Top10[0].Trend4h);
        Assert.Single(result.Value.Latest.Stage2!.Top3);
        Assert.Equal(82.5m, result.Value.Latest.Stage2.Top3[0].Score);
        Assert.Equal("WAIT", result.Value.Latest.Stage3!.Decision);
    }

    [Fact]
    public async Task GroqHunterViewModel_PopulatesFunnelAndTelemetryCorrectly()
    {
        var mockClient = new TestMockApiClient_Screening();
        var vm = new GroqHunterViewModel(mockClient);

        await vm.RefreshScreeningLogAsync();

        Assert.Equal("LIVE", vm.FreshnessStatus);
        Assert.Equal("#00E676", vm.FreshnessStatusColor);
        Assert.Contains("LIVE /api/autotrader/screening-log", vm.DataProvenance);
        Assert.NotNull(vm.LastSuccessfulUpdateTimeUtc);
        Assert.Equal("cycle-verified-100", vm.ActiveCycleId);
        Assert.Equal("ZECUSDT", vm.LatestSelectedSymbol);
        Assert.Equal("WAIT", vm.FinalSetup?.Decision);
        Assert.Single(vm.Top10Candidates);
        Assert.Equal("ZECUSDT", vm.Top10Candidates[0].Symbol);
        Assert.Single(vm.Top3Candidates);
        Assert.Equal("ZECUSDT", vm.Top3Candidates[0].Symbol);
        Assert.Single(vm.CycleHistory);
    }

    [Fact]
    public async Task GroqHunterViewModel_EmptyScreeningCycle_MarksLiveAwaitingCycle()
    {
        var mockClient = new TestMockApiClient_Empty();
        var vm = new GroqHunterViewModel(mockClient);

        await vm.RefreshScreeningLogAsync();

        Assert.Equal("LIVE", vm.FreshnessStatus);
        Assert.Equal("#00E676", vm.FreshnessStatusColor);
        Assert.Contains("Awaiting initial cycle", vm.DataProvenance);
        Assert.NotNull(vm.LastSuccessfulUpdateTimeUtc);
    }

    [Fact]
    public async Task GroqHunterViewModel_ApiError_MarksStale_PreservesData_ShowsReason()
    {
        // 1. Initial successful poll
        var mockClient = new TestMockApiClient_Screening();
        var vm = new GroqHunterViewModel(mockClient);
        await vm.RefreshScreeningLogAsync();

        Assert.Single(vm.Top10Candidates);
        string lastSync = vm.LastSuccessfulUpdateTimeString;

        // 2. Subsequent poll fails with HTTP 503
        mockClient.FailScreening = true;
        await vm.RefreshScreeningLogAsync();

        // 3. Status must be STALE (#FF9D00), existing candidates preserved, and reason clearly explained
        Assert.Equal("STALE", vm.FreshnessStatus);
        Assert.Equal("#FF9D00", vm.FreshnessStatusColor);
        Assert.Contains("STALE", vm.DataProvenance);
        Assert.Contains(lastSync, vm.DataProvenance);
        Assert.Contains("Backend unavailable", vm.DataProvenance);
        Assert.Single(vm.Top10Candidates); // Candidates preserved!
    }

    [Fact]
    public async Task GroqHunterViewModel_MalformedJson_HandlesGracefullyWithoutCrash()
    {
        var mockClient = new TestMockApiClient_Malformed();
        var vm = new GroqHunterViewModel(mockClient);

        await vm.RefreshScreeningLogAsync();

        Assert.Equal("STALE", vm.FreshnessStatus);
        Assert.Equal("#FF9D00", vm.FreshnessStatusColor);
        Assert.Contains("STALE", vm.DataProvenance);
        Assert.Contains("Malformed JSON payload", vm.DataProvenance);
    }

    private class TestMockApiClient_Screening : TestMockApiClient
    {
        public bool FailScreening { get; set; }

        public override Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
        {
            if (FailScreening)
            {
                return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Failure("Backend unavailable (503 Service Unavailable)", 503));
            }

            var cycle = new ScreeningCycleDto
            {
                CycleId = "cycle-verified-100",
                TimestampUtc = "2026-10-27T21:30:00Z",
                Status = "REJECTED",
                Stage1 = new Stage1ScreeningDto
                {
                    TotalScreenedPairs = 638,
                    Top10 = new List<Top10CandidateDto>
                    {
                        new() { Symbol = "ZECUSDT", MarkPrice = 1292.23m, Change24hPct = 5.65m }
                    }
                },
                Stage2 = new Stage2ScreeningDto
                {
                    Top3 = new List<Top3CandidateDto>
                    {
                        new() { Rank = 1, Symbol = "ZECUSDT", Score = 88.0m, Thesis = "Momentum breakout" }
                    }
                },
                Stage3 = new Stage3ScreeningDto
                {
                    Decision = "WAIT",
                    Symbol = "ZECUSDT",
                    Reasoning = "Equilibrium dealing range position"
                }
            };

            return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto
            {
                Status = "SUCCESS",
                ActiveCycle = new ScreeningCycleDto { CycleId = "cycle-verified-100" },
                Latest = cycle,
                History = new List<ScreeningCycleDto> { cycle },
                TimestampUtc = "2026-10-27T21:30:00Z"
            }));
        }
    }

    private class TestMockApiClient_Empty : TestMockApiClient
    {
        public override Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto
            {
                Status = "SUCCESS",
                ActiveCycle = new ScreeningCycleDto { CycleId = "cycle-init-0" },
                Latest = null,
                History = new List<ScreeningCycleDto>(),
                TimestampUtc = "2026-10-27T21:30:00Z"
            }));
        }
    }

    private class TestMockApiClient_Malformed : TestMockApiClient
    {
        public override Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Failure("Malformed JSON payload: unexpected character at line 1", 400));
        }
    }
}
