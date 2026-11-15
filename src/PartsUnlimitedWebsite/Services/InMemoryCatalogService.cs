using PartsUnlimited.Models;

namespace PartsUnlimited.Services;

/// <summary>Каталог в памяти: для учебного проекта база данных не нужна.</summary>
public sealed class InMemoryCatalogService : ICatalogService
{
    private readonly IReadOnlyList<Product> _products;

    // Единственный публичный конструктор: контейнер DI не должен подставлять
    // пустой IEnumerable<Product> вместо каталога по умолчанию.
    public InMemoryCatalogService()
    {
        _products = DefaultProducts;
    }

    public static IReadOnlyList<Product> DefaultProducts { get; } =
    [
        new(1, "LUB-001", "Моторное масло 5W-30, 4 л", "Масла и жидкости", "Toyota", 3490m, 25),
        new(2, "FLT-104", "Масляный фильтр", "Фильтры", "Toyota", 690m, 40),
        new(3, "BRK-220", "Тормозные колодки передние", "Тормозная система", "Volkswagen", 4290m, 12),
        new(4, "BRK-221", "Тормозной диск передний", "Тормозная система", "Volkswagen", 5890m, 0),
        new(5, "LGT-310", "Фара головного света, левая", "Освещение", "Kia", 12900m, 3),
        new(6, "BAT-060", "Аккумулятор 60 А·ч", "Электрооборудование", "Lada", 7990m, 8),
        new(7, "WPR-055", "Щётки стеклоочистителя, комплект", "Кузов", "Hyundai", 1290m, 0),
        new(8, "FLT-118", "Салонный фильтр угольный", "Фильтры", "Kia", 990m, 31),
    ];

    public IReadOnlyList<Product> GetAll() => _products;

    public IReadOnlyList<Product> GetFeatured(int count)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(count);
        return _products
            .Where(p => p.InStock)
            .OrderByDescending(p => p.Price)
            .ThenBy(p => p.Id)
            .Take(count)
            .ToList();
    }

    public IReadOnlyList<Product> Search(string? query, string? carMake = null)
    {
        if (string.IsNullOrWhiteSpace(query) && string.IsNullOrWhiteSpace(carMake))
        {
            return [];
        }

        IEnumerable<Product> result = _products;
        if (!string.IsNullOrWhiteSpace(query))
        {
            var q = query.Trim();
            result = result.Where(p =>
                p.Title.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                p.Sku.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                p.Category.Contains(q, StringComparison.OrdinalIgnoreCase));
        }

        if (!string.IsNullOrWhiteSpace(carMake))
        {
            var make = carMake.Trim();
            result = result.Where(p => string.Equals(p.CarMake, make, StringComparison.OrdinalIgnoreCase));
        }

        return result.OrderBy(p => p.Title, StringComparer.CurrentCulture).ToList();
    }

    public IReadOnlyList<string> GetCarMakes() =>
        _products.Select(p => p.CarMake).Distinct().Order(StringComparer.Ordinal).ToList();

    public Product? GetById(int id) => _products.FirstOrDefault(p => p.Id == id);
}
