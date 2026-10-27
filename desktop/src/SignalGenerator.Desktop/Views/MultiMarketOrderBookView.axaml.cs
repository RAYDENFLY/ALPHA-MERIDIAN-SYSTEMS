using System;
using Avalonia.Controls;
using Avalonia.Controls.Selection;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.ViewModels;

namespace SignalGenerator.Desktop.Views;

public partial class MultiMarketOrderBookView : UserControl
{
    public MultiMarketOrderBookView()
    {
        InitializeComponent();
    }

    private void OnTileSymbolSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (sender is not ComboBox combo || combo.DataContext is not MarketBookTileModel tile ||
            combo.SelectedItem is not string symbol || DataContext is not MultiMarketOrderBookViewModel vm)
            return;

        vm.ChangeSymbol(new Tuple<MarketBookTileModel, string>(tile, symbol));
    }
}
