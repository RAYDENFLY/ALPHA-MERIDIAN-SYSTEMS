using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MarketMonitorViewModelTests
{
    private class MockPollingCoordinator : IPollingCoordinator
    {
#pragma warning disable CS0067
        public bool IsConnected => true;
        public DateTime? LastPollTimeUtc => DateTime.UtcNow;
        public string? LastError => null;
        public bool IsAutoPollingEnabled => true;

        public event Action<SystemStatusDto>? OnStatusUpdated;
        public event Action<LiveSessionEnvelopeDto>? OnLiveSessionUpdated;
        public event Action<JournalSummaryDto>? OnJournalUpdated;
        public event Action<string>? OnPollingError;
        public event Action<bool>? OnConnectionStatusChanged;
#pragma warning restore CS0067

        public void Start() { }
        public void Stop() { }
        public Task TriggerPollNowAsync() => Task.CompletedTask;
        public void SetInterval(int seconds) { }
        public void Dispose() { }
    }

    private class MockApiClient : TestMockApiClient
    {
        private readonly Func<Task<ApiResult<ScreenerResponseDto>>>? _handler;

        public MockApiClient(Func<Task<ApiResult<ScreenerResponseDto>>>? handler = null)
        {
            _handler = handler;
        }

        public override Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
            string? search = null,
            int page = 1,
            int pageSize = 25,
            string? exchanges = null,
            string? sortBy = "volume_desc",
            CancellationToken ct = default)
        {
            if (_handler != null)
            {
                return _handler();
            }

            var items = new List<MarketAssetTickerDto>
            {
                new MarketAssetTickerDto
                {
                    NormalizedSymbol = "BTCUSDT",
                    BenchmarkPrice = 67500.0m,
                    TotalVolume24hUsdt = 150000000.0m,
                    TotalOpenInterestUsdt = 50000000.0m,
                    PriceChange24hPct = 2.45m,
                    VenueCount = 3,
                    ContractType = "PERPETUAL",
                    Venues = new Dictionary<string, VenueDetailDto>
                    {
                        { "asterdex", new VenueDetailDto { Exchange = "asterdex", ContractType = "PERPETUAL" } },
                        { "binance", new VenueDetailDto { Exchange = "binance", ContractType = "PERPETUAL" } }
                    }
                },
                new MarketAssetTickerDto
                {
                    NormalizedSymbol = "SOLUSDT",
                    BenchmarkPrice = 180.50m,
                    TotalVolume24hUsdt = 45000000.0m,
                    TotalOpenInterestUsdt = 15000000.0m,
                    PriceChange24hPct = 5.80m,
                    VenueCount = 3,
                    ContractType = "PERPETUAL",
                    Venues = new Dictionary<string, VenueDetailDto>
                    {
                        { "bybit", new VenueDetailDto { Exchange = "bybit", ContractType = "PERPETUAL" } },
                        { "hyperliquid", new VenueDetailDto { Exchange = "hyperliquid", ContractType = "PERPETUAL" } }
                    }
                }
            };

            return Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
            {
                Items = items,
                TotalCount = 2,
                Page = 1,
                PageSize = 25,
                TotalPages = 1
            }));
        }
    }

    [Fact]
    public async Task MarketMonitorViewModel_LoadsMatrixAndComputesLeaders()
    {
        var client = new MockApiClient();
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coord);

        await vm.LoadMarketMatrixAsync();

        Assert.Equal(2, vm.Markets.Count);
        Assert.Equal(2, vm.TotalMarketsCount);
        Assert.Equal("BTCUSDT", vm.HighestVolumeSymbol);
        Assert.Contains("150,000,000", vm.HighestVolumeText);
        Assert.Equal("SOLUSDT", vm.TopGainerSymbol);
        Assert.Equal("+5.80%", vm.TopGainerChange);
        Assert.True(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);
        Assert.False(vm.ShowGenericEmptyNotice);
        Assert.False(vm.HasError);
    }

    [Fact]
    public void MarketMonitorViewModel_SpotOnlyDataset_CorrectlyDisplaysSpotAndOmitsOi()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        var spotData = new List<MarketAssetTickerDto>
        {
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "BTCUSDT",
                BenchmarkPrice = 67200.0m,
                TotalVolume24hUsdt = 80000000.0m,
                ContractType = "SPOT",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "binance", new VenueDetailDto { Exchange = "binance", ContractType = "SPOT" } }
                }
            },
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "ETHUSDT",
                BenchmarkPrice = 2550.0m,
                TotalVolume24hUsdt = 35000000.0m,
                ContractType = "SPOT",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "bybit", new VenueDetailDto { Exchange = "bybit", ContractType = "SPOT" } }
                }
            }
        };

        vm.SeedMarkets(spotData);

        // Under SPOT filter
        vm.SelectMarketType("SPOT");
        Assert.Equal(2, vm.Markets.Count);
        Assert.True(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);
        Assert.Equal("N/A (SPOT)", vm.Markets[0].FormattedOi);
        Assert.Equal("SPOT", vm.Markets[0].InstrumentType);

        // Under PERPETUAL filter, spot items should not match
        vm.SelectMarketType("PERPETUAL");
        Assert.Empty(vm.Markets);
        Assert.True(vm.ShowGenericEmptyNotice);
        Assert.False(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);

        // Under ALL filter, both are shown
        vm.SelectMarketType("ALL");
        Assert.Equal(2, vm.Markets.Count);
        Assert.True(vm.ShowDataTable);
    }

    [Fact]
    public void MarketMonitorViewModel_PerpetualOnlyDataset_SpotFilterTriggersSpotUnsupportedState()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        var perpData = new List<MarketAssetTickerDto>
        {
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "BTCUSDT",
                BenchmarkPrice = 67500.0m,
                TotalVolume24hUsdt = 120000000.0m,
                TotalOpenInterestUsdt = 45000000.0m,
                ContractType = "PERPETUAL",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "asterdex", new VenueDetailDto { Exchange = "asterdex", ContractType = "PERPETUAL" } }
                }
            }
        };

        vm.SeedMarkets(perpData);

        // Default ALL
        Assert.Single(vm.Markets);
        Assert.True(vm.ShowDataTable);

        // Switch to SPOT
        vm.SelectMarketType("SPOT");
        Assert.Empty(vm.Markets);
        Assert.True(vm.ShowSpotUnavailable);
        Assert.False(vm.ShowDataTable);
        Assert.False(vm.ShowGenericEmptyNotice);
        Assert.False(vm.HasError);
        Assert.Contains("Connected exchange adapters", vm.SpotUnavailableMessage);
        Assert.Contains("NO SPOT INSTRUMENTS AVAILABLE ON CONNECTED VENUES", vm.EmptyMessage);
    }

    [Theory]
    [InlineData("ASTERDEX", "AsterDEX is an on-chain decentralized perpetual exchange")]
    [InlineData("BINANCE", "Binance USD-M Futures adapter is active")]
    [InlineData("BYBIT", "Bybit Linear Perpetuals adapter is active")]
    [InlineData("HYPERLIQUID", "Hyperliquid Perpetuals adapter is active")]
    [InlineData("ALL", "Connected exchange adapters")]
    public void MarketMonitorViewModel_VenueSpecificSpotExplanation(string venue, string expectedKeyword)
    {
        var explanation = MarketMonitorViewModel.GetSpotUnavailableExplanation(venue);
        Assert.Contains(expectedKeyword, explanation);
    }

    [Fact]
    public void MarketMonitorViewModel_MixedVenueDataset_FilterMatrix()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        var mixedData = new List<MarketAssetTickerDto>
        {
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "ASTER-PERP",
                BenchmarkPrice = 1.25m,
                TotalVolume24hUsdt = 5000000.0m,
                ContractType = "PERPETUAL",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "asterdex", new VenueDetailDto { Exchange = "asterdex", ContractType = "PERPETUAL" } }
                }
            },
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "BNB-PERP",
                BenchmarkPrice = 600.0m,
                TotalVolume24hUsdt = 25000000.0m,
                ContractType = "PERPETUAL",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "binance", new VenueDetailDto { Exchange = "binance", ContractType = "PERPETUAL" } }
                }
            },
            new MarketAssetTickerDto
            {
                NormalizedSymbol = "BNB-SPOT",
                BenchmarkPrice = 600.0m,
                TotalVolume24hUsdt = 10000000.0m,
                ContractType = "SPOT",
                Venues = new Dictionary<string, VenueDetailDto>
                {
                    { "binance", new VenueDetailDto { Exchange = "binance", ContractType = "SPOT" } }
                }
            }
        };

        vm.SeedMarkets(mixedData);

        // Filter ASTERDEX + PERPETUAL -> 1 item
        vm.SelectVenue("ASTERDEX");
        vm.SelectMarketType("PERPETUAL");
        Assert.Single(vm.Markets);
        Assert.Equal("ASTER-PERP", vm.Markets[0].NormalizedSymbol);

        // Filter ASTERDEX + SPOT -> 0 items, triggers Spot unavailable for AsterDEX
        vm.SelectMarketType("SPOT");
        Assert.Empty(vm.Markets);
        Assert.True(vm.ShowSpotUnavailable);
        Assert.Contains("AsterDEX is an on-chain decentralized perpetual exchange", vm.SpotUnavailableMessage);

        // Filter BINANCE + SPOT -> 1 item (BNB-SPOT)
        vm.SelectVenue("BINANCE");
        vm.SelectMarketType("SPOT");
        Assert.Single(vm.Markets);
        Assert.Equal("BNB-SPOT", vm.Markets[0].NormalizedSymbol);
        Assert.True(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);
    }

    [Fact]
    public async Task MarketMonitorViewModel_ApiUnavailable_TriggersDistinctErrorState()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient(() => Task.FromResult(ApiResult<ScreenerResponseDto>.Failure("Connection refused: 127.0.0.1:8000")));
        var vm = new MarketMonitorViewModel(client, coord);

        await vm.LoadMarketMatrixAsync();

        Assert.True(vm.HasError);
        Assert.True(vm.IsApiUnavailableError);
        Assert.False(vm.IsDataMappingError);
        Assert.False(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);
        Assert.Equal("Connection refused: 127.0.0.1:8000", vm.ErrorMessage);
    }

    [Fact]
    public async Task MarketMonitorViewModel_DataMappingError_TriggersDistinctMappingErrorState()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient(() => Task.FromResult(ApiResult<ScreenerResponseDto>.Failure("JSON deserialization error: invalid token at offset 42")));
        var vm = new MarketMonitorViewModel(client, coord);

        await vm.LoadMarketMatrixAsync();

        Assert.True(vm.HasError);
        Assert.True(vm.IsDataMappingError);
        Assert.False(vm.IsApiUnavailableError);
        Assert.False(vm.ShowDataTable);
    }

    [Fact]
    public async Task MarketMonitorViewModel_EmptyResponse_TriggersGenericEmptyNotice()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient(() => Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
        {
            Items = new List<MarketAssetTickerDto>(),
            TotalCount = 0
        })));
        var vm = new MarketMonitorViewModel(client, coord);

        await vm.LoadMarketMatrixAsync();

        Assert.False(vm.HasError);
        Assert.False(vm.ShowDataTable);
        Assert.False(vm.ShowSpotUnavailable);
        Assert.True(vm.ShowGenericEmptyNotice);
        Assert.Equal(0, vm.TotalMarketsCount);
    }

    [Fact]
    public void MarketMonitorViewModel_MissingOptionalFields_FormatsGracefully()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        var sparseItem = new MarketAssetTickerDto
        {
            NormalizedSymbol = "TESTUSDT",
            BenchmarkPrice = null,
            TotalVolume24hUsdt = 0,
            TotalOpenInterestUsdt = 0,
            PriceChange24hPct = null,
            BestSpreadBps = null,
            Venues = null,
            ContractType = null
        };

        vm.SeedMarkets(new[] { sparseItem });

        Assert.Single(vm.Markets);
        var item = vm.Markets[0];
        Assert.Equal("--", item.FormattedPrice);
        Assert.Equal("--", item.FormattedVolume);
        Assert.Equal("--", item.FormattedOi);
        Assert.Equal("--", item.FormattedSpread);
        Assert.Equal("--", item.FormattedChange);
        Assert.Equal("PERPETUAL", item.InstrumentType);
    }

    [Fact]
    public void MarketMonitorViewModel_ResetFilters_RestoresDefaultState()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        vm.SelectVenue("BINANCE");
        vm.SelectMarketType("SPOT");
        vm.SearchFilter = "SOL";

        Assert.Equal("BINANCE", vm.SelectedVenue);
        Assert.Equal("SPOT", vm.SelectedMarketType);
        Assert.Equal("SOL", vm.SearchFilter);

        vm.ResetFilters();

        Assert.Equal("ALL", vm.SelectedVenue);
        Assert.Equal("ALL", vm.SelectedMarketType);
        Assert.Equal(string.Empty, vm.SearchFilter);
    }

    [Fact]
    public void MarketMonitorViewModel_ActiveButtonStyling_ReflectsState()
    {
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(new MockApiClient(), coord);

        // Initially ALL
        Assert.Equal("#1F2836", vm.TypeAllBackground);
        Assert.Equal("#FF9D00", vm.TypeAllForeground);
        Assert.Equal("#0A0D10", vm.TypeSpotBackground);

        // Switch to SPOT
        vm.SelectMarketType("SPOT");
        Assert.Equal("#0A0D10", vm.TypeAllBackground);
        Assert.Equal("#1F2836", vm.TypeSpotBackground);
        Assert.Equal("#FFB300", vm.TypeSpotForeground);

        // Venue styling
        Assert.Equal("#1F2836", vm.VenueAllBackground);
        vm.SelectVenue("ASTERDEX");
        Assert.Equal("#0A0D10", vm.VenueAllBackground);
        Assert.Equal("#1F2836", vm.VenueAsterBackground);
        Assert.Equal("#00E676", vm.VenueAsterForeground);
    }

    [Fact]
    public async Task LiveBackend_ScreenerMarkets_DeserializationAudit()
    {
        var client = new ApiClient(new System.Net.Http.HttpClient(), "http://127.0.0.1:8000");
        var res = await client.GetScreenerMarketsAsync(page: 1, pageSize: 10);
        Assert.True(res.IsSuccess, $"API failure: {res.ErrorMessage}");
        Assert.NotNull(res.Value);
        Assert.NotEmpty(res.Value.Items);
        Assert.All(res.Value.Items, item => Assert.Equal("PERPETUAL", item.InstrumentType));
    }
}
