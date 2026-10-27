using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class AlertsViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private readonly List<AlertItemDto> _allAlerts = new();

    // Tab Switcher: ACTIVE or HISTORY
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsActiveTab))]
    [NotifyPropertyChangedFor(nameof(IsHistoryTab))]
    private string _selectedTab = "ACTIVE";

    public bool IsActiveTab => SelectedTab == "ACTIVE";
    public bool IsHistoryTab => SelectedTab == "HISTORY";

    // Filtering
    [ObservableProperty]
    private string _searchFilter = string.Empty;

    [ObservableProperty]
    private string _selectedConditionFilter = "ALL";

    [ObservableProperty]
    private string _selectedStatusFilter = "ALL";

    // Collections
    [ObservableProperty]
    private ObservableCollection<AlertItemDto> _alerts = new();

    [ObservableProperty]
    private ObservableCollection<AlertEventDto> _historyEvents = new();

    [ObservableProperty]
    private AlertItemDto? _selectedAlert;

    // Telemetry & Metrics
    [ObservableProperty]
    private int _totalAlertsCount;

    [ObservableProperty]
    private int _activeAlertsCount;

    [ObservableProperty]
    private int _triggeredCount;

    [ObservableProperty]
    private int _staleCount;

    [ObservableProperty]
    private string _discordWebhookStatus = "MONITORING (DISCORD NOTIFICATIONS CONFIGURED)";

    [ObservableProperty]
    private string _lastRefreshUtc = "--:--:--";

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private bool _isSubmitting;

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    private string? _successMessage;

    [ObservableProperty]
    private string _emptyMessage = "NO ACTIVE ALERTS CONFIGURED — CLICK [+ CREATE ALERT] TO ADD AN ALERT LEVEL";

    // Create / Edit Drawer State
    [ObservableProperty]
    private bool _isFormOpen;

    [ObservableProperty]
    private string _formMode = "CREATE"; // "CREATE" or "EDIT"

    [ObservableProperty]
    private string _formAlertId = string.Empty;

    [ObservableProperty]
    private string _formSymbol = string.Empty;

    [ObservableProperty]
    private string _formTargetPriceText = string.Empty;

    [ObservableProperty]
    private string _formDirection = "PRICE_ABOVE"; // "PRICE_ABOVE" or "PRICE_BELOW"

    [ObservableProperty]
    private string _formPriceSource = "MARK_PRICE"; // "MARK_PRICE" or "LAST_PRICE"

    [ObservableProperty]
    private string _formAlertType = "ONE_OFF"; // "ONE_OFF" or "RECURRING"

    [ObservableProperty]
    private string _formNote = string.Empty;

    [ObservableProperty]
    private string? _formValidationMessage;

    // Delete Confirmation State
    [ObservableProperty]
    private bool _isDeleteConfirmOpen;

    [ObservableProperty]
    private string _deletingAlertId = string.Empty;

    [ObservableProperty]
    private string _deletingAlertSummary = string.Empty;

    public AlertsViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _ = LoadAlertsAsync();
    }

    [RelayCommand]
    public void SelectTab(string tab)
    {
        SelectedTab = tab.ToUpperInvariant();
        ErrorMessage = null;
        SuccessMessage = null;
        if (IsHistoryTab && HistoryEvents.Count == 0)
        {
            _ = LoadHistoryAsync();
        }
    }

    [RelayCommand]
    public void OpenCreateForm()
    {
        FormMode = "CREATE";
        FormAlertId = string.Empty;
        FormSymbol = SelectedAlert?.Symbol ?? "BTCUSDT";
        FormTargetPriceText = string.Empty;
        FormDirection = "PRICE_ABOVE";
        FormPriceSource = "MARK_PRICE";
        FormAlertType = "ONE_OFF";
        FormNote = string.Empty;
        FormValidationMessage = null;
        ErrorMessage = null;
        SuccessMessage = null;
        IsFormOpen = true;
    }

    [RelayCommand]
    public void OpenEditForm(AlertItemDto alert)
    {
        if (alert == null) return;
        FormMode = "EDIT";
        FormAlertId = alert.AlertId;
        FormSymbol = alert.Symbol;
        FormTargetPriceText = alert.TargetPrice.ToString("F4", CultureInfo.InvariantCulture);
        FormDirection = alert.Direction ?? "PRICE_ABOVE";
        FormPriceSource = alert.PriceSource ?? "MARK_PRICE";
        FormAlertType = alert.AlertType ?? "ONE_OFF";
        FormNote = alert.Note ?? string.Empty;
        FormValidationMessage = null;
        ErrorMessage = null;
        SuccessMessage = null;
        IsFormOpen = true;
    }

    [RelayCommand]
    public void CloseForm()
    {
        IsFormOpen = false;
        FormValidationMessage = null;
    }

    [RelayCommand]
    public async Task SubmitFormAsync()
    {
        if (IsSubmitting) return;

        // Validation
        FormValidationMessage = null;
        ErrorMessage = null;
        SuccessMessage = null;

        var symbol = FormSymbol?.Trim().ToUpperInvariant() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(symbol))
        {
            FormValidationMessage = "VALIDATION ERROR: Symbol is required (e.g. BTCUSDT).";
            return;
        }

        if (!decimal.TryParse(FormTargetPriceText?.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out var targetPrice) || targetPrice <= 0)
        {
            FormValidationMessage = "VALIDATION ERROR: Target price must be a valid positive number.";
            return;
        }

        if (string.IsNullOrWhiteSpace(FormDirection) || (FormDirection != "PRICE_ABOVE" && FormDirection != "PRICE_BELOW"))
        {
            FormValidationMessage = "VALIDATION ERROR: Crossing direction must be PRICE_ABOVE or PRICE_BELOW.";
            return;
        }

        IsSubmitting = true;

        try
        {
            if (FormMode == "CREATE")
            {
                var req = new AlertCreateRequestDto
                {
                    Symbol = symbol,
                    TargetPrice = targetPrice,
                    Direction = FormDirection,
                    PriceSource = FormPriceSource,
                    AlertType = FormAlertType,
                    Note = FormNote?.Trim() ?? string.Empty
                };

                var res = await _apiClient.CreateAlertAsync(req);
                if (res.IsSuccess && res.Value != null)
                {
                    SuccessMessage = $"ALERT CREATED: {res.Value.AlertId} ({res.Value.Symbol} @ ${res.Value.FormattedTarget})";
                    IsFormOpen = false;
                    await LoadAlertsAsync();
                }
                else
                {
                    ErrorMessage = res.ErrorMessage ?? "Failed to create alert.";
                    FormValidationMessage = ErrorMessage;
                }
            }
            else // EDIT
            {
                var req = new AlertUpdateRequestDto
                {
                    TargetPrice = targetPrice,
                    Direction = FormDirection,
                    PriceSource = FormPriceSource,
                    AlertType = FormAlertType,
                    Note = FormNote?.Trim() ?? string.Empty
                };

                var res = await _apiClient.UpdateAlertAsync(FormAlertId, req);
                if (res.IsSuccess && res.Value != null)
                {
                    SuccessMessage = $"ALERT UPDATED: {res.Value.AlertId} ({res.Value.Symbol} @ ${res.Value.FormattedTarget})";
                    IsFormOpen = false;
                    await LoadAlertsAsync();
                }
                else
                {
                    ErrorMessage = res.ErrorMessage ?? "Failed to update alert.";
                    FormValidationMessage = ErrorMessage;
                }
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Exception submitting alert: {ex.Message}";
            FormValidationMessage = ErrorMessage;
        }
        finally
        {
            IsSubmitting = false;
        }
    }

    [RelayCommand]
    public async Task ToggleAlertAsync(AlertItemDto alert)
    {
        if (alert == null || IsSubmitting) return;
        IsSubmitting = true;
        ErrorMessage = null;
        SuccessMessage = null;

        try
        {
            var res = await _apiClient.ToggleAlertAsync(alert.AlertId);
            if (res.IsSuccess && res.Value != null)
            {
                var state = res.Value.IsActive ? "ACTIVATED" : "DISABLED";
                SuccessMessage = $"ALERT {alert.AlertId} {state}.";
                await LoadAlertsAsync();
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? $"Failed to toggle alert {alert.AlertId}.";
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Error toggling alert: {ex.Message}";
        }
        finally
        {
            IsSubmitting = false;
        }
    }

    [RelayCommand]
    public void PromptDeleteAlert(AlertItemDto alert)
    {
        if (alert == null) return;
        DeletingAlertId = alert.AlertId;
        DeletingAlertSummary = $"{alert.Symbol} @ ${alert.FormattedTarget} ({alert.Direction})";
        IsDeleteConfirmOpen = true;
        ErrorMessage = null;
        SuccessMessage = null;
    }

    [RelayCommand]
    public void CancelDelete()
    {
        IsDeleteConfirmOpen = false;
        DeletingAlertId = string.Empty;
        DeletingAlertSummary = string.Empty;
    }

    [RelayCommand]
    public async Task ConfirmDeleteAsync()
    {
        if (string.IsNullOrWhiteSpace(DeletingAlertId) || IsSubmitting) return;
        IsSubmitting = true;
        var alertId = DeletingAlertId;

        try
        {
            var res = await _apiClient.DeleteAlertAsync(alertId);
            if (res.IsSuccess)
            {
                SuccessMessage = $"ALERT DELETED: {alertId}.";
                IsDeleteConfirmOpen = false;
                DeletingAlertId = string.Empty;
                DeletingAlertSummary = string.Empty;
                await LoadAlertsAsync();
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? $"Failed to delete alert {alertId}.";
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Error deleting alert: {ex.Message}";
        }
        finally
        {
            IsSubmitting = false;
        }
    }

    [RelayCommand]
    public async Task LoadAlertsAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var alertsTask = _apiClient.GetAlertsAsync();
            var statusTask = _apiClient.GetAlertStatusAsync();

            await Task.WhenAll(alertsTask, statusTask);

            var res = await alertsTask;
            if (res.IsSuccess && res.Value != null)
            {
                _allAlerts.Clear();
                _allAlerts.AddRange(res.Value);

                TotalAlertsCount = _allAlerts.Count;
                ActiveAlertsCount = _allAlerts.Count(a => a.IsActive);
                TriggeredCount = _allAlerts.Count(a => a.Status.Equals("TRIGGERED", StringComparison.OrdinalIgnoreCase));
                StaleCount = _allAlerts.Count(a => a.Status.Equals("STALE", StringComparison.OrdinalIgnoreCase) || a.IsStale);

                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
                ApplyFilters();
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to load alerts from backend.";
            }

            var statusRes = await statusTask;
            if (statusRes.IsSuccess && statusRes.Value != null)
            {
                TotalAlertsCount = statusRes.Value.TotalAlerts;
                ActiveAlertsCount = statusRes.Value.ActiveAlerts;
                TriggeredCount = statusRes.Value.TriggeredAlerts;
                StaleCount = statusRes.Value.StaleAlerts;
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    public async Task LoadHistoryAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var res = await _apiClient.GetAlertHistoryAsync(limit: 50);
            if (res.IsSuccess && res.Value != null)
            {
                HistoryEvents.Clear();
                foreach (var evt in res.Value)
                {
                    HistoryEvents.Add(evt);
                }
                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to load alert trigger history.";
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    partial void OnSearchFilterChanged(string value) => ApplyFilters();
    partial void OnSelectedConditionFilterChanged(string value) => ApplyFilters();
    partial void OnSelectedStatusFilterChanged(string value) => ApplyFilters();

    public void ApplyFilters()
    {
        Alerts.Clear();

        IEnumerable<AlertItemDto> query = _allAlerts;

        // Search Filter
        if (!string.IsNullOrWhiteSpace(SearchFilter))
        {
            var q = SearchFilter.Trim().ToUpperInvariant();
            query = query.Where(a => a.Symbol.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                                     a.AlertId.Contains(q, StringComparison.OrdinalIgnoreCase));
        }

        // Condition Filter
        if (SelectedConditionFilter != "ALL")
        {
            query = query.Where(a => a.Direction.Equals(SelectedConditionFilter, StringComparison.OrdinalIgnoreCase));
        }

        // Status Filter
        if (SelectedStatusFilter != "ALL")
        {
            query = query.Where(a => a.Status.Equals(SelectedStatusFilter, StringComparison.OrdinalIgnoreCase));
        }

        foreach (var a in query)
        {
            Alerts.Add(a);
        }

        if (Alerts.Count == 0)
        {
            EmptyMessage = !string.IsNullOrWhiteSpace(SearchFilter)
                ? $"NO ALERTS FOUND MATCHING '{SearchFilter.ToUpperInvariant()}'"
                : "NO CONFIGURED PRICE ALERTS — CLICK [+ CREATE ALERT] TO ADD AN ALERT LEVEL";
        }
    }
}
