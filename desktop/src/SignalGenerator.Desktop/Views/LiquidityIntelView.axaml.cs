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
    }

    private void OnResetChartClicked(object? sender, RoutedEventArgs e)
    {
        var chart = this.FindControl<CandlestickChartControl>("MainCandlestickChart");
        chart?.ResetView();
    }
}
