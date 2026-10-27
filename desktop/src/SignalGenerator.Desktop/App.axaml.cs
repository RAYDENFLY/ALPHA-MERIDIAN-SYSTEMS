using System;
using System.Net.Http;
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Microsoft.Extensions.DependencyInjection;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using SignalGenerator.Desktop.Views;

namespace SignalGenerator.Desktop;

public partial class App : Application
{
    public IServiceProvider? Services { get; private set; }

    public override void Initialize()
    {
        AvaloniaXamlLoader.Load(this);
    }

    public override void OnFrameworkInitializationCompleted()
    {
        var services = new ServiceCollection();
        ConfigureServices(services);
        Services = services.BuildServiceProvider();

        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var mainVm = Services.GetRequiredService<MainViewModel>();
            desktop.MainWindow = new MainWindow
            {
                DataContext = mainVm
            };
        }

        base.OnFrameworkInitializationCompleted();
    }

    private static void ConfigureServices(IServiceCollection services)
    {
        // Core HTTP Client
        services.AddSingleton(sp =>
        {
            var handler = new HttpClientHandler
            {
                ServerCertificateCustomValidationCallback = (_, _, _, _) => true
            };
            return new HttpClient(handler)
            {
                Timeout = TimeSpan.FromSeconds(10)
            };
        });

        // Services
        services.AddSingleton<ISettingsService, SettingsService>();
        services.AddSingleton<IApiClient, ApiClient>();
        services.AddSingleton<IPollingCoordinator, PollingCoordinator>();

        // ViewModels
        services.AddSingleton<OverviewViewModel>();
        services.AddSingleton<MarketMonitorViewModel>();
        services.AddSingleton<ScreenerViewModel>();
        services.AddSingleton<LiquidityIntelViewModel>();
        services.AddSingleton<GroqHunterViewModel>();
        services.AddSingleton<JournalViewModel>();
        services.AddSingleton<AlertsViewModel>();
        services.AddSingleton<MarketParticipantsViewModel>();
        services.AddSingleton<CryptoNewsViewModel>();
        services.AddSingleton<SettingsViewModel>();
        services.AddSingleton<MainViewModel>();
    }
}
