using System;
using System.Collections.Generic;
using System.Net;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Views;

public partial class VelaChartHostView : UserControl
{
    private bool _initialized;
    private VelaAssetServer? _assetServer;

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
            var mode = string.Equals(Environment.GetEnvironmentVariable("AMS_VELA_HARNESS"), "1", StringComparison.Ordinal)
                ? "harness" : "production";
            _assetServer = new VelaAssetServer(Path.GetDirectoryName(page)!);
            _assetServer.Start();
            var uri = new Uri(_assetServer.BaseUri, "index.html?host=" + mode);
            Browser.Navigate(uri);
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
        _assetServer?.Dispose();
        _assetServer = null;
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

    internal sealed class VelaAssetServer : IDisposable
    {
        private static readonly IReadOnlyDictionary<string, string> MimeTypes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            [".html"] = "text/html; charset=utf-8", [".css"] = "text/css; charset=utf-8",
            [".js"] = "text/javascript; charset=utf-8", [".json"] = "application/json",
            [".png"] = "image/png", [".svg"] = "image/svg+xml", [".woff2"] = "font/woff2"
        };
        private readonly string _root;
        private HttpListener? _listener;
        private CancellationTokenSource? _cts;
        private Task? _serveTask;
        public Uri BaseUri { get; private set; } = null!;

        public VelaAssetServer(string root)
        {
            _root = Path.GetFullPath(root);
            if (!Directory.Exists(_root)) throw new DirectoryNotFoundException(_root);
        }

        public void Start()
        {
            if (_listener != null) return;
            using var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            var port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            _listener = new HttpListener();
            var prefix = $"http://127.0.0.1:{port}/";
            _listener.Prefixes.Add(prefix);
            _listener.Start();
            BaseUri = new Uri(prefix, UriKind.Absolute);
            _cts = new CancellationTokenSource();
            _serveTask = Task.Run(() => ServeAsync(_cts.Token));
        }

        private async Task ServeAsync(CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested && _listener != null)
            {
                HttpListenerContext context;
                try { context = await _listener.GetContextAsync().WaitAsync(cancellationToken); }
                catch { break; }
                _ = Task.Run(() => RespondAsync(context), cancellationToken);
            }
        }

        private async Task RespondAsync(HttpListenerContext context)
        {
            var response = context.Response;
            try
            {
                var relative = Uri.UnescapeDataString(context.Request.Url?.AbsolutePath.TrimStart('/') ?? string.Empty);
                if (string.IsNullOrWhiteSpace(relative) || relative.Contains('\0') || relative.Contains("..", StringComparison.Ordinal))
                { response.StatusCode = 404; return; }
                var full = Path.GetFullPath(Path.Combine(_root, relative));
                if (!full.StartsWith(_root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                { response.StatusCode = 404; return; }
                var extension = Path.GetExtension(full);
                if (!MimeTypes.TryGetValue(extension, out var mime) || !File.Exists(full))
                { response.StatusCode = 404; return; }
                response.ContentType = mime;
                response.Headers["X-Content-Type-Options"] = "nosniff";
                response.Headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self' http://127.0.0.1; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'";
                var bytes = await File.ReadAllBytesAsync(full);
                response.ContentLength64 = bytes.Length;
                await response.OutputStream.WriteAsync(bytes);
            }
            catch { response.StatusCode = 500; }
            finally { response.Close(); }
        }

        public void Dispose()
        {
            _cts?.Cancel();
            try { _listener?.Stop(); _listener?.Close(); } catch { }
            _listener = null;
            _cts?.Dispose(); _cts = null;
            _serveTask = null;
        }
    }
}
