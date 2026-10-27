using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class ScreenerViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _searchQuery = string.Empty;

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    private int _totalAssetsCount;

    [ObservableProperty]
    private int _currentPage = 1;

    [ObservableProperty]
    private int _totalPages = 1;

    public ObservableCollection<MarketAssetTickerDto> Markets { get; } = new();

    public ScreenerViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
    }

    [RelayCommand]
    public async Task LoadMarketsAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var res = await _apiClient.GetScreenerMarketsAsync(SearchQuery, CurrentPage, 25);
            if (res.IsSuccess && res.Value != null)
            {
                Markets.Clear();
                foreach (var item in res.Value.Items)
                {
                    Markets.Add(item);
                }
                TotalAssetsCount = res.Value.TotalCount;
                TotalPages = Math.Max(1, res.Value.TotalPages);
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to load screener markets.";
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Error: {ex.Message}";
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    public async Task NextPageAsync()
    {
        if (CurrentPage < TotalPages)
        {
            CurrentPage++;
            await LoadMarketsAsync();
        }
    }

    [RelayCommand]
    public async Task PrevPageAsync()
    {
        if (CurrentPage > 1)
        {
            CurrentPage--;
            await LoadMarketsAsync();
        }
    }
}
