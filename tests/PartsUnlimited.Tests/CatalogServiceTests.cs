using PartsUnlimited.Models;
using PartsUnlimited.Services;

namespace PartsUnlimited.Tests;

public class CatalogServiceTests
{
    private readonly InMemoryCatalogService _catalog = new();

    [Fact]
    public void GetFeatured_ReturnsOnlyProductsInStock()
    {
        var featured = _catalog.GetFeatured(10);

        Assert.NotEmpty(featured);
        Assert.All(featured, p => Assert.True(p.InStock));
    }

    [Fact]
    public void GetFeatured_RespectsCountAndOrdersByPriceDescending()
    {
        var featured = _catalog.GetFeatured(3);

        Assert.Equal(3, featured.Count);
        Assert.Equal(featured.OrderByDescending(p => p.Price).Select(p => p.Id), featured.Select(p => p.Id));
    }

    [Fact]
    public void Search_NullQuery_ReturnsEmptyListInsteadOfError()
    {
        // Регрессионный тест к ошибке «Ошибка 500 при пустом поисковом запросе».
        Assert.Empty(_catalog.Search(null));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Search_BlankQuery_ReturnsEmptyList(string query)
    {
        Assert.Empty(_catalog.Search(query));
    }

    [Theory]
    [InlineData("фильтр", 2)]
    [InlineData("ФИЛЬТР", 2)]
    [InlineData("brk-22", 2)]
    [InlineData("Тормозная", 2)]
    [InlineData("несуществующая деталь", 0)]
    public void Search_MatchesTitleSkuAndCategoryIgnoringCase(string query, int expected)
    {
        var result = _catalog.Search(query);

        Assert.Equal(expected, result.Count);
    }

    [Fact]
    public void Search_FiltersByCarMake()
    {
        var result = _catalog.Search(null, "kia");

        Assert.Equal(2, result.Count);
        Assert.All(result, p => Assert.Equal("Kia", p.CarMake));
    }

    [Fact]
    public void Search_CombinesQueryAndCarMake()
    {
        var result = _catalog.Search("фильтр", "Toyota");

        var product = Assert.Single(result);
        Assert.Equal("FLT-104", product.Sku);
    }

    [Fact]
    public void GetCarMakes_ReturnsDistinctSortedMakes()
    {
        var makes = _catalog.GetCarMakes();

        Assert.Equal(makes.Distinct().Order(StringComparer.Ordinal), makes);
        Assert.Contains("Lada", makes);
    }

    [Fact]
    public void GetById_UnknownId_ReturnsNull()
    {
        Assert.Null(_catalog.GetById(999));
    }

    [Fact]
    public void Product_WithZeroStock_IsNotInStock()
    {
        var product = new Product(1, "X", "Деталь", "Категория", "Lada", 100m, 0);

        Assert.False(product.InStock);
    }
}
