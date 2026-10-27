using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IPersonalJournalService
{
    string DatabasePath { get; }
    Task InitializeAsync();
    Task<List<PersonalTradeRecord>> GetAllTradesAsync();
    Task<PersonalTradeRecord?> GetTradeByIdAsync(string tradeId);
    Task<bool> SaveTradeAsync(PersonalTradeRecord trade);
    Task<bool> DeleteTradeAsync(string tradeId);
    Task<Dictionary<DateTime, decimal>> GetDailyNetPnlAsync(int year, int month);
    Task<decimal> GetMonthlyNetPnlAsync(int year, int month);
}
