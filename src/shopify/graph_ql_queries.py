#


RETURN_ORDERS_QUERY = """
query ($first: Int, $after: String, $query: String) {
  orders(
    first: $first
    after: $after
    query: $query
  ) {

    pageInfo {
        hasNextPage
        hasPreviousPage
        startCursor
        endCursor
    }

    edges {
        cursor
        node {
            id
            name
            tags
            transactions {
              id
              kind
              gateway
              amountSet {
                presentmentMoney {
                  amount
                }
              }
            }
            totalPriceSet {
              shopMoney {
                amount
                currencyCode
              }
              presentmentMoney {
                amount
                currencyCode
              }
            }
            lineItems (first: $first){
              nodes {
                id,
                quantity
                refundableQuantity   
              }
            }
            fulfillments {
                id
                name
                totalQuantity
                displayStatus
                requiresShipping
                trackingInfo(first: 10) {
                    number
                    company
                    url
                }
            }
            returns(first: $first) {
              nodes {
                id
                name
                reverseFulfillmentOrders(first: 5) {
                  nodes {
                    reverseDeliveries(first: 5) {
                      nodes {
                        deliverable {
                          ... on ReverseDeliveryShippingDeliverable {
                            tracking {
                              carrierName
                              number
                              url
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
          }
        }
    }
  }
}
"""


REFUND_CREATE_MUTATION = """
mutation RefundLineItem($input: RefundInput!) {
  refundCreate(input: $input) {
    refund { 
      id
      note
      createdAt
      totalRefundedSet { 
          presentmentMoney { 
              amount
              currencyCode
          }
      }
    }
    userErrors { 
      field
      message
    }
  }
}
"""
