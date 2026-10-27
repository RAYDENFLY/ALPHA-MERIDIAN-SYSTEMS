using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IFxRateService
{
    Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default);
    Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default);
}
