using PartsUnlimited.Models;

namespace PartsUnlimited.Services;

public interface ICatalogService
{
    IReadOnlyList<Product> GetAll();

    /// <summary>Рекомендуемые товары для главной страницы: только в наличии.</summary>
    IReadOnlyList<Product> GetFeatured(int count);

    /// <summary>Поиск по названию, артикулу и категории; пустой запрос возвращает пустой список.</summary>
    IReadOnlyList<Product> Search(string? query, string? carMake = null);

    IReadOnlyList<string> GetCarMakes();

    Product? GetById(int id);
}
