using Avalonia.Controls;
using Avalonia.Input;
using SignalGenerator.Desktop.ViewModels;

namespace SignalGenerator.Desktop.Views;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        KeyDown += OnMainWindowKeyDown;
    }

    private void OnMainWindowKeyDown(object? sender, KeyEventArgs e)
    {
        if (DataContext is not MainViewModel vm) return;

        // Ctrl+R for Refresh
        if (e.Key == Key.R && (e.KeyModifiers & KeyModifiers.Control) != 0)
        {
            _ = vm.RefreshAllAsync();
            e.Handled = true;
            return;
        }

        switch (e.Key)
        {
            case Key.F1:
                vm.Navigate("Overview");
                e.Handled = true;
                break;
            case Key.F2:
                vm.Navigate("MarketIntelligence");
                e.Handled = true;
                break;
            case Key.F3:
                vm.Navigate("MultiMarketOrderBook");
                e.Handled = true;
                break;
            case Key.F4:
                vm.Navigate("TradingWorkstation");
                e.Handled = true;
                break;
            case Key.F5:
                vm.Navigate("Alerts");
                e.Handled = true;
                break;
            case Key.F6:
                vm.Navigate("LiquidityIntel");
                e.Handled = true;
                break;
            case Key.F7:
                vm.Navigate("GroqHunter");
                e.Handled = true;
                break;
            case Key.F8:
                vm.Navigate("Journal");
                e.Handled = true;
                break;
            case Key.F9:
                vm.Navigate("MarketParticipants");
                e.Handled = true;
                break;
            case Key.F10:
                vm.Navigate("CryptoNews");
                e.Handled = true;
                break;
            case Key.F11:
                vm.Navigate("Settings");
                e.Handled = true;
                break;
            case Key.F12:
                _ = vm.RefreshAllAsync();
                e.Handled = true;
                break;
        }
    }
}
