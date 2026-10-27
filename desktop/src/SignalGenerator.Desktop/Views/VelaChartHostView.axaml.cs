using System;
using System.IO;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Views;

public partial class VelaChartHostView : UserControl
{
    private bool _initialized;

    public VelaChartHostView()
    {
        InitializeComponent();
        AttachedToVisualTree += OnAttached;
        DetachedFromVisualTree += OnDetached;
    }

    private void OnAttached(object? sender, VisualTreeAttachmentEventArgs e)
    {
        if (_initialized) return;
        _initialized = true;
        var root = AppContext.BaseDirectory;
        var page = Path.Combine(root, "VelaChart", "index.html");
        if (!File.Exists(page))
        {
            ShowError("Vela assets are not packaged. Expected: " + page);
            return;
        }

        try
        {
            Browser.Navigate(new Uri(page + "?host=production"));
        }
        catch (Exception ex)
        {
            ShowError("WebView2/Vela initialization failed: " + ex.Message);
        }
    }

    private void OnDetached(object? sender, VisualTreeAttachmentEventArgs e)
    {
        // NativeWebView owns the platform adapter and releases it when detached.
        _initialized = false;
        try { Browser.Stop(); } catch { }
    }

    private void ShowError(string message)
    {
        ErrorText.Text = message;
        ErrorPanel.IsVisible = true;
    }

    public bool SendSnapshot(VelaBridgeMessage message)
    {
        if (message is null || message.Version != 1 || !string.Equals(message.Type, "vela.snapshot", StringComparison.Ordinal)) return false;
        var json = JsonSerializer.Serialize(message);
        var escaped = JsonSerializer.Serialize(json);
        try
        {
            Browser.InvokeScript($"window.amsReceiveBridgeMessage && window.amsReceiveBridgeMessage(JSON.parse({escaped}));");
            return true;
        }
        catch { return false; }
    }
}
