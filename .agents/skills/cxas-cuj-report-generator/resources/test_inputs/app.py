"""Simple module for processing restaurant orders."""


def process_order(order_id, items):
    """Processes a restaurant order.

    Args:
        order_id: The ID of the order.
        items: A list of items ordered.

    Returns:
        A dictionary with order status.
    """
    print(f"Processing order {order_id} with items: {items}")
    return {
        "status": "success",
        "message": "Order placed",
        "order_id": order_id,
    }


if __name__ == "__main__":
    # Example usage
    sample_order = process_order("1234", ["pizza", "salad"])
    print(sample_order)
