using Microsoft.AspNetCore.Mvc;
using PartsUnlimited.Controllers;
using PartsUnlimited.Models;
using PartsUnlimited.Services;

namespace PartsUnlimited.Tests;

public class HomeControllerTests
{
    private readonly HomeController _controller = new(new InMemoryCatalogService());

    [Fact]
    public void Index_ShowsFeaturedProducts()
    {
        var result = Assert.IsType<ViewResult>(_controller.Index());

        var model = Assert.IsAssignableFrom<IReadOnlyList<Product>>(result.Model);
        Assert.Equal(HomeController.FeaturedCount, model.Count);
    }

    [Fact]
    public void Product_UnknownId_ReturnsNotFound()
    {
        Assert.IsType<NotFoundResult>(_controller.Product(12345));
    }

    [Fact]
    public void Product_KnownId_ReturnsProductView()
    {
        var result = Assert.IsType<ViewResult>(_controller.Product(3));

        var product = Assert.IsType<Product>(result.Model);
        Assert.Equal("BRK-220", product.Sku);
    }

    [Fact]
    public void Search_PassesQueryAndMakesToView()
    {
        var result = Assert.IsType<ViewResult>(_controller.Search("масло", null));

        Assert.Equal("масло", result.ViewData["Query"]);
        Assert.NotNull(result.ViewData["Makes"]);
        var model = Assert.IsAssignableFrom<IReadOnlyList<Product>>(result.Model);
        Assert.Single(model);
    }
}
