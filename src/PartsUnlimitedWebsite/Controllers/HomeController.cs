using Microsoft.AspNetCore.Mvc;
using PartsUnlimited.Services;

namespace PartsUnlimited.Controllers;

public sealed class HomeController(ICatalogService catalog) : Controller
{
    public const int FeaturedCount = 4;

    public IActionResult Index() => View(catalog.GetFeatured(FeaturedCount));

    public IActionResult Search(string? q, string? make)
    {
        ViewData["Query"] = q;
        ViewData["Make"] = make;
        ViewData["Makes"] = catalog.GetCarMakes();
        return View(catalog.Search(q, make));
    }

    public IActionResult Product(int id)
    {
        var product = catalog.GetById(id);
        return product is null ? NotFound() : View(product);
    }

    public IActionResult Error() => View();
}
