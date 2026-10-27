using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IFxRateService
{
    Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default);
    Task<FxRateSnapshotDto> GetLatestRateAsync(string pair, CancellationToken ct = default);
    Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default);
    Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string pair, string period, CancellationToken ct = default);
    IReadOnlyList<FxPairConfig> GetSupportedPairs();
}
