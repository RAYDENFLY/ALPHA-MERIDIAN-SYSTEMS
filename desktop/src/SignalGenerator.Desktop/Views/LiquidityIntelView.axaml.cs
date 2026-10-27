using Avalonia.Controls;
using Avalonia.Interactivity;
using SignalGenerator.Desktop.Controls;

namespace SignalGenerator.Desktop.Views;

public partial class LiquidityIntelView : UserControl
{
    public LiquidityIntelView()
    {
        InitializeComponent();

        var resetBtn = this.FindControl<Button>("ResetChartBtn");
        if (resetBtn != null)
        {
            resetBtn.Click += OnResetChartClicked;
        }
        DataContextChanged += (_, _) => ApplyChartMode();
        ApplyChartMode();
    }

    private void OnResetChartClicked(object? sender, RoutedEventArgs e)
    {
        var chart = this.FindControl<CandlestickChartControl>("MainCandlestickChart");
        chart?.ResetView();
    }

    private void ApplyChartMode()
    {
        if (DataContext is ViewModels.LiquidityIntelViewModel vm && MainCandlestickChart != null)
            MainCandlestickChart.IsVisible = !vm.UseVelaChart;
    }
}
