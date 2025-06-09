import logging
from fastapi import FastAPI, HTTPException, Depends
from typing import Dict, Optional, List
from langchain_openai import ChatOpenAI
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities.sql_database import SQLDatabase
from database import SingletonSQLDatabase
from custom_datatypes import ModelInput, ModelInput_1
from langchain_community.agent_toolkits.sql.base import create_sql_agent
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
from langchain.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, SystemMessage
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from collections import defaultdict
import ast

import os
load_dotenv()
# OpenAI API Key
# openai_api_key = os.getenv("OPEN_API_KEY")

AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION")

# Initialize FastAPI application
app = FastAPI()
# Function to keep the database connection alive
def keep_connection_alive():
    try:
        db = SingletonSQLDatabase.get_instance()  # Get the singleton database instance
        db.run("SELECT 1")  # Execute a simple query to keep the connection alive
        logging.info("Database connection kept alive.")
    except Exception as e:
        logging.error("Error in keep_connection_alive:", exc_info=True)

# Initialize APScheduler
scheduler = BackgroundScheduler()

# Schedule the keep_connection_alive task to run every 10 seconds
scheduler.add_job(keep_connection_alive, 'interval', seconds=999999)

# Function to get the database connection via dependency injection
def get_db_connection():
    return SingletonSQLDatabase.get_instance()



def fetch_top_vendors(
    db, 
    item_names: Optional[List[str]] = None, 
    port_names: Optional[List[str]] = None, 
    item_ids: Optional[List[str]] = None, 
    port_ids: Optional[List[str]] = None
):
    try:
        # Trim spaces from user inputs if names are provided
        if item_names:
            item_names = [name.strip() for name in item_names]
        if port_names:
            port_names = [port.strip() for port in port_names]

        # Build the item condition: use IDs if provided, otherwise use names
        if item_ids:
            item_ids_escaped = [f"'{str(item_id).strip()}'" for item_id in item_ids]
            item_condition = f"ITEM_ID IN ({', '.join(item_ids_escaped)})"
        elif item_names:
            item_names = [name.lower() for name in item_names]
            item_names_escaped = [f"'{name}'" for name in item_names]
            item_condition = f"LOWER(LTRIM(RTRIM(ITEM_DESCRIPTION))) IN ({', '.join(item_names_escaped)})"
        else:
            return []

        # Build the port condition: use IDs if provided, otherwise use names
        if port_ids:
            port_ids_escaped = [f"'{str(port_id).strip()}'" for port_id in port_ids]
            port_condition = f"SchdDeliveryPortID IN ({', '.join(port_ids_escaped)})"
        elif port_names:
            port_names = [port.lower() for port in port_names]
            port_names_escaped = [f"'{port}'" for port in port_names]
            port_condition = f"LOWER(LTRIM(RTRIM(SchdDeliveryPort))) IN ({', '.join(port_names_escaped)})"
        else:
            return []

        # Combine conditions
        condition = f"{item_condition} AND {port_condition}"

        query = f"""
            SELECT LTRIM(RTRIM(SchdDeliveryPort)), 
                   LTRIM(RTRIM(ITEM_DESCRIPTION)), 
                   VendorName, 
                   VendorCode, 
                   COUNT(*) as OrderCount
            FROM Common.Tbl_Vw_Dm_GDB_Items_UniqueID_vendor_integrated
            WHERE {condition}
            GROUP BY SchdDeliveryPort, ITEM_DESCRIPTION, VendorName, VendorCode
            ORDER BY SchdDeliveryPort, ITEM_DESCRIPTION, OrderCount DESC
        """
        
        result = db.run(query)
        logging.info(f"Raw query result: {result}")

        if isinstance(result, str):
            try:
                result = ast.literal_eval(result)
            except (SyntaxError, ValueError):
                result = []

        columns = ["SchdDeliveryPort", "ITEM_DESCRIPTION", "VendorName", "VendorCode", "OrderCount"]
        result_dicts = [dict(zip(columns, row)) for row in result]

        # Dictionary to store top 2 vendors for each item at each port
        port_item_vendors = defaultdict(lambda: defaultdict(list))

        for row in result_dicts:
            port = row["SchdDeliveryPort"]
            item = row["ITEM_DESCRIPTION"]
            vendor_info = {
                "VendorName": row["VendorName"],
                "vendorCode": row["VendorCode"],
                "totalOrderCount": row["OrderCount"],
                "Description": f"Total {item} ordered at {port}: {row['OrderCount']}"
            }

            # Store top 2 vendors per item at each port
            if len(port_item_vendors[port][item]) < 2:
                port_item_vendors[port][item].append(vendor_info)

        # Construct the final result
        final_result = []
        for port, items in port_item_vendors.items():
            for item, vendors in items.items():
                for vendor in vendors:
                    final_result.append({
                        "Port": port,
                        "Item": item,
                        "VendorName": vendor["VendorName"],
                        "vendorCode": vendor["vendorCode"],
                        "totalOrderCount": vendor["totalOrderCount"],
                        "Description": vendor["Description"]
                    })

        return final_result

    except Exception as e:
        logging.error("Error fetching top vendors", exc_info=True)
        return []
    
@app.post("/query__vendor/")
async def handle_query(userinput: ModelInput_1, db: SQLDatabase = Depends(get_db_connection)) -> Dict:
    try:
        response_data = {"response": {}, "top_vendors": [], "vendor_suggestions": ""}

        # Check if item/port details are provided via names or IDs
        if (userinput.item_name and userinput.port_name) or (userinput.item_id and userinput.port_id):
            top_vendors = fetch_top_vendors(
                db,
                item_names=userinput.item_name,
                port_names=userinput.port_name,
                item_ids=userinput.item_id,
                port_ids=userinput.port_id
            )

            if top_vendors:
                unique_vendors = {f"{vendor['VendorName']} ({vendor['vendorCode']})" for vendor in top_vendors}
                response_data = {
                    "response": "",  # Keep response as an empty string when data exists
                    "vendor_suggestions": ", ".join(unique_vendors),
                    "top_vendors": top_vendors
                }
            else:
                response_data = {
                    "response": "No top vendors found for the specified criteria.",
                    "vendor_suggestions": "",
                    "top_vendors": []
                }
            return response_data

   
        if userinput.user_query and userinput.user_query.strip():
            # llm = ChatOpenAI(
            #     model="gpt-4o",
            #     temperature=0,
            #     streaming=True,
            #     verbose=False,
            #     openai_api_key=openai_api_key
            # )

            llm = AzureChatOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=OPENAI_API_VERSION,
        )
    
            # SQL Agent Setup
            # toolkit = SQLDatabaseToolkit(llm=llm, db=db)
            agent_executor = create_sql_agent(llm, db=db, agent_type="openai-tools", verbose=True)

            # Metadata and Instruction Context
            column_metadata = """
            - **SMC**: The SMC (ship management centres) are specialized facilities or organizations responsible for overseeing the efficient and effective operation of a fleet of ships.
            - **Account_Code**: The unique code assigned to the account for the purpose of financial tracking or reporting.
            - **Account_Name**: The name associated with the account, typically representing the entity or individual that holds the account.
            - **Account_Details**: Additional details or descriptions about the account, including terms, conditions, and other relevant information.
            - **Analysis_Code**: The code used for categorizing or analyzing transactions or items, typically used for reporting or analysis.
            - **Analysis_Name**: The name associated with the analysis code, providing more context about the categorization or analysis purpose.
            - **Sub_account_Code**: The unique code for a sub-account, which is a subdivision of a main account, allowing more detailed tracking of financial transactions.
            - **Sub_Account_Name**: The name associated with the sub-account, describing its purpose or the entity it represents.
            - **alt_Account_Code**: An alternative account code used for tracking or categorization purposes, often used for cross-referencing.
            - **alt_Account_Name**: An alternative name for the account, typically used for different accounting systems or standards.
            - **alt_Analysis_Code**: An alternative analysis code used to categorize or group transactions for reporting or analysis purposes.
            - **alt_Analysis_Name**: An alternative name for the analysis code, providing a different reference to the categorization or analysis process.
            - **alt_Sub_account_Code**: An alternative code for a sub-account, used in cases where different systems or standards require separate coding.
            - **alt_Sub_Account_Name**: An alternative name for the sub-account, providing a reference for cross-system compatibility or reporting.
            - **VesselName**: The name of the vessel involved in the transaction, purchase, or order.
            - **Vessel_Objectid**: A unique identifier for the vessel, often used in databases to associate records to a specific vessel.
            - **Vendorid**: A unique identifier for the vendor or supplier, used for managing the vendor relationship and transactions.
            - **OwnerID**: The identifier for the owner of the vessel or equipment, used for tracking ownership.
            - **Primary_Manager_id**: The ID of the primary manager responsible for overseeing the vessel or equipment operations.
            - **Vessel_Id1**: An additional or alternate identifier for the vessel, possibly used for legacy systems or specific classifications.
            - **pocategory_id**: The unique identifier for the purchase order category, used to classify different types of purchase orders.
            - **Maker_id**: The unique identifier for the manufacturer of the item or equipment, used for tracking and managing products from specific makers.
            - **ITEM_CATEGORY_id**: The identifier for the category of the item, used to classify items into different categories for better management and reporting.
            - **IMONumber**: The International Maritime Organization (IMO) number is a unique identifier assigned to ships for maritime safety and legal purposes.
            - **OWNERNAME**: The name of the owner of the item, vessel, or equipment.
            - **pocategory**: The category of the purchase order, used to define the type or classification of the order (e.g., maintenance, procurement, etc.).
            - **PoNumber**: The unique identifier for the purchase order, used to track and reference the order in the procurement system.
            - **APPROVAL_FLAG**: A flag indicating whether the purchase order has been approved. Typically a boolean value (e.g., True or False).
            - **ApprovedDate**: The date on which the purchase order was approved, marking the official authorization of the order.
            - **POSENTDATE**: The date when the purchase order was entered into the system, indicating the creation or registration date.
            - **poitemcount**: The total number of items in the purchase order, summarizing the count of individual items listed in the order.
            - **Title**: The title of the item or transaction, often describing the nature of the purchase order or the item involved.
            - **VENDORCODE**: A unique code assigned to the vendor or supplier, used for vendor identification and classification.
            - **VendorName**: The name of the vendor or supplier providing the goods or services in the transaction.
            - **VENDORCOUNTRY**: The country where the vendor or supplier is located, important for logistics, legal, and reporting purposes.
            - **VENDOREMAIL**: The email address of the vendor, used for communications related to the transaction or order.
            - **VENDORPHONE**: The phone number of the vendor, used for contacting the supplier for queries or updates.
            - **VENDORAPPROVALSTATUS**: The approval status of the vendor, indicating whether the vendor is approved for transactions or is under review.
            - **BaseCurrency**: The primary currency used in the purchase order or financial transaction, defining the standard for pricing and value.
            - **BaseAmount**: The total amount in the base currency for the purchase order, excluding any adjustments, taxes, or fees.
            - **SchdDeliveryPort**: The scheduled delivery port for the goods or equipment, marking the intended arrival location for the shipment.
            - **REQ_NOS**: The required number of items or units specified in the purchase order, often used for inventory or fulfillment purposes.
            - **ENQNOS**: The associated number of the inquiry, possibly related to a request for quotation or inquiry process before purchase.
            - **GRNNO_AGENT_WAREHOUSE**: The Goods Receipt Note (GRN) number associated with the agent's warehouse, tracking goods receipt in the warehouse system.
            - **GRNNO_VESSEL**: The Goods Receipt Note (GRN) number related to the vessel, indicating the goods received on board the vessel.
            - **EQUIPMENTCODE**: A unique code assigned to a piece of equipment for identification and tracking.
            - **EQUIPMENTNAME**: The name of the equipment being referenced in the transaction or order.
            - **ParentCode**: The code for the parent item or category, often used to group items or transactions under a common parent classification.
            - **ParentName**: The name associated with the parent item or category, used to give more context to the classification of the item.
            - **Maker**: The manufacturer of the item, equipment, or vessel, responsible for the creation or production of the item.
            - **EQUIPMENT_TYPE**: The type or classification of the equipment, used for grouping or categorizing similar types of equipment.
            - **DrawingNo**: The number associated with the technical drawing or blueprint of the item or equipment, used for reference in design or manufacturing.
            - **SerialNo**: The unique serial number assigned to a specific piece of equipment, used for identification and tracking.
            - **MODEL**: The model name or number associated with the item, used for identifying the specific version or variant of the product.
            - **PART_NUMBER**: The unique identifier for a specific part of the equipment, used for inventory and replacement tracking.
            - **ITEM_ID**: A unique identifier for the item in the system, used to track the specific item in inventory or procurement.
            - **ITEM_DESCRIPTION**: A detailed description of the item, providing information about its features, specifications, and use cases.
            - **SERVICE_DESCRIPTION**: The description of the service provided with the item, detailing the nature of the service.
            - **DRAWING_NUMBER**: A reference number for a technical or engineering drawing related to the item, used for design and manufacturing.
            - **WEIGHT**: The weight of the item, typically used for shipping, inventory, and logistical purposes.
            - **PACKING_UOM**: The unit of measurement for the packing of the item, such as box, pallet, etc.
            - **UNIT_PRICE**: The price per unit of the item, used for calculating costs, pricing, and invoicing.
            - **QUANTITY**: The number of items or units in the purchase order or transaction.
            - **VENDOR_REMARKS**: Any additional remarks or comments from the vendor, typically related to terms, conditions, or special considerations.
            - **REMARKS_TO_VENDOR**: Notes or remarks addressed to the vendor, providing additional instructions or requests.
            - **ITEM_CATEGORY**: The category or classification of the item, often used for grouping items into different types or segments.
            - **ITEM_SECTION**: A specific section or subgroup within the item category, used for further classification and reporting.
            - **ITEM_CODE**: A unique code assigned to the item, used for identifying and tracking it in the system.
            - **UOM**: The unit of measurement for the item, such as kilogram, meter, etc.
            - **PO_USD_VALUE**: The value of each purchase orders in USD, used for financial tracking and reporting.
            - **po_amount_usd**: The amount of the purchase order in USD, often used for financial reconciliation.
            - **MD_REQUIRED**: Indicates whether a Material Data (MD) is required for the item.
            - **SDoC_REQUIRED**: Indicates whether a Supplier Declaration of Conformity (SDoC) is required for the item.
            - **UNIT_PRICE_USD**: The price per unit of the item in USD, used for international pricing or currency conversions.
            - **Received_Qty**: The quantity of items received, used for inventory tracking and logistics.
            - **Po_ApprovedDate**: The date when the purchase order was approved, used for tracking approval timelines.
            - **Po_Title**: The title or name associated with the purchase order, often used for categorization or easy reference.
            - **EQUIPMENT_ParentCode**: The parent code for the equipment, used for hierarchical tracking.
            - **EQUIPMENT_ParentName**: The parent name for the equipment, used for categorizing and tracking equipment in a group.
            - **ULTIMATE_OWNER_Name**: The name of the ultimate owner of the item or equipment, representing the highest level of ownership.
            - **PartNumber**: The part number associated with the item, used for identification and inventory purposes.
            - **UnitPrice**: The price per unit of the item, used for cost calculations and invoicing.
            - **ItemDescription**: A detailed description of the item, providing information about its specifications, use cases, or features.
            - **ReceivedQuantity**: The quantity of the items that have been received against the purchase order, often used in inventory and shipment tracking.
            - **VendorAddress**: Extract the full address of the vendor, including street, building number, and any other address details. Ensure the address is complete.
            - **VendorWebsite**: Identify and extract the official website URL of the vendor. The URL should be valid and correspond to the vendor's primary website.
            - **VendorFax**: Extract the fax number associated with the vendor, if available. If not explicitly mentioned, leave this field blank.
            - **VendorCity**: Extract the city name where the vendor is located. Ensure it is specific and consistent with the address details.
            - **VendorState**: Extract the state or province associated with the vendor's address. This should match the city and ZIP code details.
            - **VendorZipCode**: Extract the postal or ZIP code from the vendor's address. Ensure the code is complete and valid.
            - **Vendor_IsSchulteCompany**: Determine if the vendor is affiliated with Schulte companies. Extract this information as a Boolean indicator ("Yes"/"No") or numeric equivalent (1/0).
            - **Vendor_Registration_Number**: Extract the vendor's unique registration or identification number as assigned by a regulatory or legal authority.
            - **Vendor_Registered_City**: Extract the city where the vendor is officially registered as a business entity. This may differ from the operational city.
            - **Vendor_Registered_Country**: Extract the country where the vendor is officially registered. Ensure it matches any associated registration details.

            """
    
            Metadata_Groupings= """
            #### **Vendor-Related Data**:
            - **Vendorid**: A unique identifier for the vendor or supplier, used for managing the vendor relationship and transactions.
            - **VendorName**: The name of the vendor or supplier providing the goods or services in the transaction.
            - **VENDORCODE**: A unique code assigned to the vendor or supplier, used for vendor identification and classification.
            - **VENDORCOUNTRY**: The country where the vendor or supplier is located, important for logistics, legal, and reporting purposes.
            - **VENDOREMAIL**: The email address of the vendor, used for communications related to the transaction or order.
            - **VENDORPHONE**: he phone number of the vendor, used for contacting the supplier for queries or updates.
            - **VENDORAPPROVALSTATUS**: The approval status of the vendor, indicating whether the vendor is approved for transactions or is under review.
            - **VENDOR_REMARKS**: Any additional remarks or comments from the vendor, typically related to terms, conditions, or special considerations.
            - **REMARKS_TO_VENDOR**: Notes or remarks addressed to the vendor, providing additional instructions or requests.
            - **Primary_Manager_id**: The ID of the primary manager responsible for overseeing the vessel or equipment operations.
            - **VendorAddress**: Extract the full address of the vendor, including street, building number, and any other address details. Ensure the address is complete.
            - **VendorWebsite**: Identify and extract the official website URL of the vendor. The URL should be valid and correspond to the vendor's primary website.
            - **VendorFax**: Extract the fax number associated with the vendor, if available. If not explicitly mentioned, leave this field blank.
            - **VendorCity**: Extract the city name where the vendor is located. Ensure it is specific and consistent with the address details.
            - **VendorState**: Extract the state or province associated with the vendor's address. This should match the city and ZIP code details.
            - **VendorZipCode**: Extract the postal or ZIP code from the vendor's address. Ensure the code is complete and valid.
            - **Vendor_IsSchulteCompany**: Determine if the vendor is affiliated with Schulte companies. Extract this information as a Boolean indicator ("Yes"/"No") or numeric equivalent (1/0).
            - **Vendor_Registration_Number**: Extract the vendor's unique registration or identification number as assigned by a regulatory or legal authority.
            - **Vendor_Registered_City**: Extract the city where the vendor is officially registered as a business entity. This may differ from the operational city.
            - **Vendor_Registered_Country**: Extract the country where the vendor is officially registered. Ensure it matches any associated registration details.

            
            #### **Purchase Order-Related Data**:
            - **PoNumber**: The unique identifier for the purchase order, used to track and reference the order in the procurement system.
            - **pocategory**: The category of the purchase order, used to define the type or classification of the order (e.g., maintenance, procurement, etc.).
            - **pocategory_id**: The unique identifier for the purchase order category, used to classify different types of purchase orders.
            - **poitemcount**: The total number of items in the purchase order, summarizing the count of individual items listed in the order.
            - **BaseCurrency**: The primary currency used in the purchase order or financial transaction, defining the standard for pricing and value.
            - **BaseAmount**: The total amount in the base currency for the purchase order, excluding any adjustments, taxes, or fees.
            - **POSENTDATE**: The date when the purchase order was entered into the system, indicating the creation or registration date.
            - **ApprovedDate**:The date on which the purchase order was approved, marking the official authorization of the order.
            - **APPROVAL_FLAG**: A flag indicating whether the purchase order has been approved. Typically a boolean value (e.g., True or False).
            - **po_amount_usd**: The amount of the purchase order in USD, often used for financial reconciliation.
            - **PO_USD_VALUE**: The value of each purchase orders in USD, used for financial tracking and reporting.
            - **Po_ApprovedDate**: The date when the purchase order was approved, used for tracking approval timelines.
            - **Po_Title**: The title or name associated with the purchase order, often used for categorization or easy reference.
            
            #### **Item and Equipment-Related Data**:
            - **ITEM_ID**: A unique identifier for the item in the system, used to track the specific item in inventory or procurement.
            - **ITEM_DESCRIPTION**: A detailed description of the item, providing information about its features, specifications, and use cases.
            - **ITEM_CATEGORY**: The category or classification of the item, often used for grouping items into different types or segments.
            - **ITEM_CATEGORY_id**: The identifier for the category of the item, used to classify items into different categories for better management and reporting.
            - **ITEM_SECTION**: A specific section or subgroup within the item category, used for further classification and reporting.
            - **ITEM_CODE**: A unique code assigned to the item, used for identifying and tracking it in the system.
            - **UNIT_PRICE**: The price per unit of the item, used for calculating costs, pricing, and invoicing.
            - **QUANTITY**: The number of items or units in the purchase order or transaction.
            - **Received_Qty**: The quantity of items received, used for inventory tracking and logistics.
            - **ReceivedQuantity**: The quantity of the items that have been received against the purchase order, often used in inventory and shipment tracking.
            - **Packing_UOM**: The unit of measurement for the packing of the item, such as box, pallet, etc.
            - **MD_REQUIRED**: Indicates whether a Material Data (MD) is required for the item.
            - **SDoC_REQUIRED**: Indicates whether a Supplier Declaration of Conformity (SDoC) is required for the item.
            - **Maker**: The manufacturer of the item, equipment, or vessel, responsible for the creation or production of the item.
            - **DrawingNo**: The number associated with the technical drawing or blueprint of the item or equipment, used for reference in design or manufacturing.
            - **DRAWING_NUMBER**: A reference number for a technical or engineering drawing related to the item, used for design and manufacturing.
            - **EQUIPMENT_TYPE**: The type or classification of the equipment, used for grouping or categorizing similar types of equipment.
            - **EQUIPMENTCODE**: A unique code assigned to a piece of equipment for identification and tracking.
            - **EQUIPMENTNAME**: The name of the equipment being referenced in the transaction or order.
            - **ParentCode**: The code for the parent item or category, often used to group items or transactions under a common parent classification.
            - **ParentName**: The name associated with the parent item or category, used to give more context to the classification of the item.
            - **SerialNo**: The unique serial number assigned to a specific piece of equipment, used for identification and tracking.
            - **MODEL**: The model name or number associated with the item, used for identifying the specific version or variant of the product.
            - **PART_NUMBER**: The unique identifier for a specific part of the equipment, used for inventory and replacement tracking.
            - **SERVICE_DESCRIPTION**: The description of the service provided with the item, detailing the nature of the service.
            - **WEIGHT**: The weight of the item, typically used for shipping, inventory, and logistical purposes.
            - **UOM**: The unit of measurement for the item, such as kilogram, meter, etc.
            - **UNIT_PRICE_USD**: The price per unit of the item in USD, used for international pricing or currency conversions.
            - **EQUIPMENT_ParentCode**: The parent code for the equipment, used for hierarchical tracking.
            - **EQUIPMENT_ParentName**: The parent name for the equipment, used for categorizing and tracking equipment in a group.
            - **PartNumber**: The part number associated with the item, used for identification and inventory purposes.
            - **UnitPrice**: The price per unit of the item, used for cost calculations and invoicing.
            - **ItemDescription**: A detailed description of the item, providing information about its specifications, use cases, or features.
            - **Title**: The title of the item or transaction, often describing the nature of the purchase order or the item involved.
            
            #### **Vessel-Related Data**:
            - **VesselName**: The name of the vessel involved in the transaction, purchase, or order.
            - **Vessel_Objectid**:A unique identifier for the vessel, often used in databases to associate records to a specific vessel.
            - **Vessel_Id1**: An additional or alternate identifier for the vessel, possibly used for legacy systems or specific classifications.
            - **GRNNO_VESSEL**: The Goods Receipt Note (GRN) number related to the vessel, indicating the goods received on board the vessel.
            - **OwnerID**: The identifier for the owner of the vessel or equipment, used for tracking ownership.
            - **OWNERNAME**: The name of the owner of the item, vessel, or equipment.
            - **ULTIMATE_OWNER_Name**: The name of the ultimate owner of the item or equipment, representing the highest level of ownership.
            - **IMONumber**: The International Maritime Organization (IMO) number is a unique identifier assigned to ships for maritime safety and legal purposes.
            - **SMC**: The SMC (ship management centres) are specialized facilities or organizations responsible for overseeing the efficient and effective operation of a fleet of ships.

            #### **Port-Related Data**:
            - **SchdDeliveryPort**: (Scheduled Delivery Port) refers to the designated port or location where the goods or equipment are planned to be delivered as per the logistics schedule. This column indicates the final intended destination for the shipment, serving as a critical data point in supply chain management and transportation planning.It provides clarity on where the shipment is expected to arrive, ensuring alignment between the sender, carrier, and receiver. This information is vital for operational efficiency, resource allocation at the port, customs clearance, and tracking the shipment’s progress against the planned delivery route.
            
            #### **Account-Related Data**:
            - **Account_Code**: The unique code assigned to the account for the purpose of financial tracking or reporting.
            - **Account_Name**: The name associated with the account, typically representing the entity or individual that holds the account.
            - **Account_Details**: Additional details or descriptions about the account, including terms, conditions, and other relevant information.
            - **Analysis_Code**: The code used for categorizing or analyzing transactions or items, typically used for reporting or analysis.
            - **Analysis_Name**: The name associated with the analysis code, providing more context about the categorization or analysis purpose.
            - **Sub_account_Code**: The unique code for a sub-account, which is a subdivision of a main account, allowing more detailed tracking of financial transactions.
            - **Sub_Account_Name**: The name associated with the sub-account, describing its purpose or the entity it represents.
            - **alt_Account_Code**: An alternative account code used for tracking or categorization purposes, often used for cross-referencing.
            - **alt_Account_Name**: An alternative name for the account, typically used for different accounting systems or standards.
            - **alt_Analysis_Code**: An alternative analysis code used to categorize or group transactions for reporting or analysis purposes.
            - **alt_Analysis_Name**: An alternative name for the analysis code, providing a different reference to the categorization or analysis process.
            - **alt_Sub_account_Code**: An alternative code for a sub-account, used in cases where different systems or standards require separate coding.
            - **alt_Sub_Account_Name**: An alternative name for the sub-account, providing a reference for cross-system compatibility or reporting.  

            #### **Inquiry and Receipt Data**:
            - **ENQNOS**: The associated number of the inquiry, possibly related to a request for quotation or inquiry process before purchase.
            - **GRNNO_AGENT_WAREHOUSE**: The Goods Receipt Note (GRN) number associated with the agent's warehouse, tracking goods receipt in the warehouse system.
            - **GRNNO_VESSEL**:The Goods Receipt Note (GRN) number related to the vessel, indicating the goods received on board the vessel.

            """

            prefix = """
            You are an advanced SQL database assistant specializing in answering user queries by interacting with the `Tbl_Vw_Dm_GDB_Items_UniqueID_vendor_integrated` table in the `Common` schema.
            ### Handling General Queries:
            - If the query is a general greeting (e.g., "Hi", "Hello", "How are you?"), respond with a polite acknowledgment:
                - Example: "Hello! How can I assist you today?"
            - For unrelated or unclear questions, politely guide the user back to database-specific queries.
                - Example: "I'm here to assist with database-related queries. How can I help?"
            ### Responsibilities:
            1. Provide **precise** and **contextually relevant** answers strictly based on the specified table and schema.
            2. Ensure **query normalization and standardization** to deliver consistent and meaningful results for similar questions.
            3. Leverage response history to avoid redundant queries, optimizing efficiency and user satisfaction.
            
            ### Query Normalization Guidelines:
            - **Input Transformation**: 
            1.Convert all input text to lowercase for case-insensitive handling.
            2.Replace punctuation characters (e.g., -, _, ,, .) with spaces for better uniformity.
            3.Remove leading and trailing whitespaces; collapse multiple spaces into a single space.
            - **String Functions**:
            1.Use SQL string functions like `LOWER()`, `TRIM()`, `REPLACE()`, and fuzzy matching (`LIKE`, `LEVENSHTEIN()`, `SOUNDEX`) to account for minor spelling errors or variations.
            - **Case Mismatch Handling**:
            1.If the data in the database is stored in a specific case (e.g., uppercase), ensure that both the input and the database column are transformed to the same case during processing.
            - **For consistent matching**: 
            1.Normalize input to match the stored case (e.g., UPPER() for uppercase or LOWER() for lowercase).
            2.Apply the same transformation on both sides of the comparison.
            3.Use case-insensitive comparisons (e.g., ILIKE for PostgreSQL, collations in MySQL).
            ### SQL Query Construction:
            1. Ensure the query adheres to the **{dialect} dialect** syntax.
            2. Use **specific columns** in the SELECT clause for precision; avoid `SELECT *`.
            - Priority for `LIMIT`:
            1. Explicit value provided by the user.
            4. Order results by **relevant columns** for clarity (e.g., `ApprovedDate DESC` for recent approvals).
            5. Validate query syntax before execution to ensure success and eliminate errors.
            6. Incorporate conditions for **filtering by user intent** and domain-specific logic (e.g., fetching purchase orders for a particular `VesselName` or `SMC`).
            7. When queried regarding **unique vendors**, the unique vendors are supposed to be calculated based on  **VENDOREMAIL**
            8. Use the **PO_USD_VALUE** column for the questions regarding the purchase.
            ### Rules of Engagement:
            - Do not perform Data Manipulation Language (DML) operations such as `INSERT`, `UPDATE`, or `DELETE`.
            - Use **Markdown format** for presenting results:
            - Include bordered tables for tabular data for better readability.
            - If the query is unrelated to the database or cannot be addressed, respond with:
            *"I'm unable to provide an answer for that. This information is not available."*
            - Handle ambiguous questions by:
            1. Politely clarifying the user's intent.
            2. Assuming the most logical interpretation when clarification isn't feasible.
            - **Tone and Style**:
            - Be professional, concise, and courteous in responses.
            - Avoid database-specific jargon unless directly relevant.
            - Use the following metadata {column_metadata} and {Metadata_Groupings}
            
            Your ultimate goal is to ensure clarity, accuracy, and user satisfaction while adhering strictly to data access and usage guidelines.


            """
            
        

            
            suffix = """
            If asked about the database structure, table design, or unavailable data, respond politely:
            *"I can answer questions from this database but cannot provide information about its structure or column names. Let me assist you with the data instead."*
            
            ### Additional Guidelines:
            1. Always validate queries against user intent:
            - Prioritize **relevance and accuracy**.
            - Use domain-specific filtering for improved results (e.g., filtering by `pocategory_id` for purchase order categories).
            2. Incorporate prompt optimization techniques:
            - Break down **complex questions** into smaller SQL components to ensure accuracy.
            - Apply **logical conditions** (e.g., combining multiple filters using `AND` or `OR`) for precise results.
            3. Handle ambiguity:
            - Clarify the query if needed.
            - Make reasonable assumptions based on the schema and metadata.
            4. Optimize performance:
            - Use indexed columns in filtering conditions to speed up queries.
            - Aggregate results when large datasets are involved (e.g., using `SUM()`, `AVG()`, `GROUP BY`).
            
            5. Present answers effectively:
            - Use **Markdown** tables with proper column headers and alignments.
            - Provide **concise summaries** when large datasets are returned.

            6. For handling big result data:
            - The result is too large to display. Please refine your query or use filters to reduce the result size to show the `top ten` results only.

            """

             # Create the prompt and messages
            human_message = HumanMessagePromptTemplate.from_template("{input}").format(input=userinput)
            messages = [
                SystemMessage(content=prefix),
                human_message,
                AIMessage(content=suffix),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]

            prompt = ChatPromptTemplate.from_messages(messages)
            agent_executor = create_sql_agent(llm, db=db, agent_type="openai-tools", verbose=True, prompt=prompt,top_k=10)

            # Execute the query
            response = agent_executor.invoke(f"Now answer this query: {userinput}")["output"]
            response_data["response"] = response
            return response_data
        return {"message": "Please provide both item names and port names for vendor analysis."}
    

    except Exception as e:
        logging.error("Error handling query:", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while processing the request.")
    

#--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


# The main query handler function
@app.post("/query__product/")
async def handle_query(userinput: ModelInput, db: SQLDatabase = Depends(get_db_connection)) -> Dict:
    
    try:
    
        llm = AzureChatOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=OPENAI_API_VERSION,
        )  

        # Initialize the SQLDatabaseToolkit with LLM and the database
        
        toolkit = SQLDatabaseToolkit(llm=llm, db=db)
        dialect = toolkit.dialect
        topk = 10

        # Construct the prompt with the provided user input
        column_metadata = """

        **Description:**
        This table contains cleansed and structured item data for equipment and parts used in various systems. It provides detailed information on item descriptions, specifications, manufacturer details, and unique identifiers to ensure accurate item tracking and data integrity.

        **Columns Metadata:**

        - **ITEM_ID**: The unique identifier assigned to each item in the dataset to ensure distinct tracking and reference.
        - **item_description**: Provides a detailed textual explanation of the item's characteristics and purpose.
        - **SPECIFICATION**: Contains technical details and standards relevant to the item, ensuring compliance and quality.
        - **PART_NUMBER**: The manufacturer-assigned code that uniquely identifies the part for cataloging and procurement.
        - **POSITION_NUMBER**: Indicates the item's placement or sequence within a system or assembly for accurate positioning.
        - **DRAWING_NUMBER**: A reference to the engineering or design drawing associated with the item, used for design verification and manufacturing.
        - **MODEL_NUMBER**: Specifies the manufacturer's model designation for the item, facilitating model-specific tracking.
        - **EQUIPMENT_ID**: Uniquely identifies the equipment to which the item belongs, supporting asset management and maintenance.
        - **equipment_name**: Provides the official name of the equipment associated with the item for easier identification.
        - **equipment_code**: A standardized code assigned to the equipment for classification and reporting purposes.
        - **equip_type_id**: Uniquely identifies the category or type of equipment, supporting classification and analysis.
        - **equip_maker_id**: A unique identifier for the manufacturer of the equipment, ensuring accurate supplier tracking.
        - **equip_model_id**: Represents the unique identifier for the specific model of the equipment, aiding in version control.
        - **maker_name**: The official name of the item's manufacturer, essential for supplier management and quality control.
        - **maker_code**: A unique code designated for each manufacturer, simplifying supplier identification.
        - **model_name**: Specifies the item's model designation, providing clarity on product variations.
        - **model_code**: A unique identifier for the model, supporting detailed tracking and categorization.
        - **equip_type_name**: Describes the equipment category, enabling clear classification of equipment types.
        - **unique_id**: A distinct 16-character identifier generated by combining the part number, drawing number, and manufacturer details. This ensures that each item is uniquely identifiable, preventing duplication and facilitating accurate tracking across systems. unique id is created based on some criterias like
            1. If same equipment type, maker name, model name is there with a part number and drawing number combination it should be the first priority for generating the Unique ID.
            2. If same equipment type, maker name, model name is there, but part number is not available and position number and drawing number is available, this combination should be the second priority for generating the Unique ID.
            3. If same equipment type, maker name, model name is there with same part number and with no position number or drawing number, and for those having item description 90 % matching, generate unique id for that combination.
            4. If same equipment type, maker name, model name is there with same position number and with no part number or drawing number, and for those having item description 90 % matching, generate unique id for that combination.
            5. If same equipment type, maker name, model name is there with same drawing number and with no part number or position number, and for those having item description 90 % matching, generate unique id for that combination. 
            6. If same equipment type, maker name, model name is there and dosen't find any of the above combinations generate unique ids for this combination.
       
        **Primary Key:**

        - **unique_id**

        **Purpose:**
        The table ensures data consistency and uniqueness for items used across equipment systems, aiding in efficient inventory management, traceability, and decision-making.



        """
 
        Metadata_Groupings = """
        #### **Item Metadata**:
        - **ITEM_ID**: A unique identifier assigned to each item, crucial for tracking and referencing within the system.
        - **item_description**: A detailed explanation of the item's physical characteristics, purpose, and usage.
        - **SPECIFICATION**: Contains the item's technical details, including materials, dimensions, and performance standards, ensuring compliance with quality requirements.
        - **PART_NUMBER**: The manufacturer's unique code assigned to the part, which is essential for procurement, cataloging, and inventory control.
        - **POSITION_NUMBER**: Indicates the item's specific location or sequence within an assembly or system, ensuring correct placement and functionality.
        - **DRAWING_NUMBER**: A reference to the design or engineering drawing of the item, critical for manufacturing, verification, and modification purposes.
        - **MODEL_NUMBER**: The manufacturer's model number, used to track the specific version or variation of the item across different systems and markets.

        #### **Equipment Metadata**:
        - **EQUIPMENT_ID**: A unique identifier for the equipment to which the item belongs, supporting asset management and maintenance tracking.
        - **equipment_name**: The official name of the equipment, which aids in quick identification and reference in maintenance logs or inventories.
        - **equipment_code**: A standardized code assigned to the equipment for ease of reporting, classification, and tracking within organizational systems.
        - **equip_type_id**: An identifier for the equipment's category or type, aiding in classification and analysis for asset management, operational planning, and reporting.
        - **equip_model_id**: A specific identifier for the model of the equipment, ensuring that the correct model is tracked and managed.
        - **equip_type_name**: Describes the general category or type of the equipment, facilitating the organization of equipment into logical groups based on function or use.
        - **equip_maker_id**: A unique identifier for the maker or manufacturer of the equipment, critical for tracking and managing suppliers, warranty claims, and quality assurance.
        
        #### **Manufacturer Metadata**:
        - **maker_name**: The official name of the item’s manufacturer, critical for supplier identification, vendor management, and warranty processing.
        - **maker_code**: A unique code for the manufacturer, simplifying the identification and tracking of suppliers across systems.
        - **model_name**: A designation for the specific model of the item, clarifying any variations or versions of the product to ensure compatibility and performance expectations.
        - **model_code**: A unique identifier for the model, helping with categorization, inventory control, and detailed tracking of specific product versions.

        #### **Unique Identifier and Tracking**:
        - **unique_id**: A 16-character identifier created by combining the PART_NUMBER, DRAWING_NUMBER, and manufacturer details. This unique ID ensures each item is traceable across systems without duplication, providing seamless integration with inventory management, maintenance, and asset tracking systems.

        """


        prefix = """
        You are an advanced SQL database assistant specializing in answering user queries by interacting with the `tbl_vw_dm_gdb_items_uniqueid_mapped` table in the `Common` schema.
        ### Handling General Queries:
        - If the query is a general greeting (e.g., "Hi", "Hello", "How are you?"), respond with a polite acknowledgment:
          - Example: "Hello! How can I assist you today?"
        - For unrelated or unclear questions, politely guide the user back to database-specific queries.
          - Example: "I'm here to assist with database-related queries. How can I help?"

        ### Responsibilities:
        1. Provide **precise** and **contextually relevant** answers strictly based on the specified table and schema.
        2. Ensure **query normalization and standardization** to deliver consistent and meaningful results for similar questions.
        3. Leverage response history to avoid redundant queries, optimizing efficiency and user satisfaction.
        
        ### Query Normalization Guidelines:
        - **Input Transformation**: 
        1.Convert all input text to lowercase for case-insensitive handling.
        2.Replace punctuation characters (e.g., -, _, ,, .) with spaces for better uniformity.
        3.Remove leading and trailing whitespaces; collapse multiple spaces into a single space.
        - **String Functions**:
        1.Use SQL string functions like `LOWER()`, `TRIM()`, `REPLACE()`, and fuzzy matching (`LIKE`, `LEVENSHTEIN()`, `SOUNDEX`) to account for minor spelling errors or variations.
        - **Case Mismatch Handling**:
        1.If the data in the database is stored in a specific case (e.g., uppercase), ensure that both the input and the database column are transformed to the same case during processing.
        - **For consistent matching**: 
        1.Normalize input to match the stored case (e.g., UPPER() for uppercase or LOWER() for lowercase).
        2.Apply the same transformation on both sides of the comparison.
        3.Use case-insensitive comparisons (e.g., ILIKE for PostgreSQL, collations in MySQL).
        ### SQL Query Construction:
        1. Ensure the query adheres to the **{dialect} dialect** syntax.
        2. Use **specific columns** in the SELECT clause for precision; avoid `SELECT *`.
        4. Order results by **relevant columns** for clarity (e.g., `ITEM_ID ASC` for ordered lists).
        5. Validate query syntax before execution to ensure success and eliminate errors.
        6. Incorporate conditions for **filtering by user intent** and domain-specific logic (e.g., filtering by `SPECIFICATION` or `DRAWING_NUMBER`).
        7. When queried regarding `unique IDs`, construct the response by adhering to the criteria specified in the `column metadata`. Ensure the `unique ID` is identified based on the combination or relevance of the columns outlined in the metadata, such as `ITEM_ID`, `SPECIFICATION`, `DRAWING_NUMBER`, and any additional fields mentioned. Only provide results that meet these criteria for uniqueness."
        8. Use the **SPECIFICATION** column for detailed item descriptions.  
        ### Rules of Engagement:
        - Do not perform Data Manipulation Language (DML) operations such as `INSERT`, `UPDATE`, or `DELETE`.
        - Use **Markdown format** for presenting results:
          - Include bordered tables for tabular data for better readability.
        - If the query is unrelated to the database or cannot be addressed, respond with:
          *"I'm unable to provide an answer for that. This information is not available."*
        - Handle ambiguous questions by:
          1. Politely clarifying the user's intent.
          2. Assuming the most logical interpretation when clarification isn't feasible.
        - **Tone and Style**:
          - Be professional, concise, and courteous in responses.
          - Avoid database-specific jargon unless directly relevant.
          - Use the following metadata {column_metadata} and {Metadata_Groupings}
        
        Your ultimate goal is to ensure clarity, accuracy, and user satisfaction while adhering strictly to data access and usage guidelines.


        """
        
       

        
        suffix = """
        If asked about the database structure, table design, or unavailable data, respond politely:
        *"I can answer questions from this database but cannot provide information about its structure or column names. Let me assist you with the data instead."*
        
        ### Additional Guidelines:
        1. Always validate queries against user intent:
           - Prioritize **relevance and accuracy**.
           - Use domain-specific filtering for improved results
        2. Incorporate prompt optimization techniques:
           - Break down **complex questions** into smaller SQL components to ensure accuracy.
           - Apply **logical conditions** (e.g., combining multiple filters using `AND` or `OR`) for precise results.
        3. Handle ambiguity:
           - Clarify the query if needed.
           - Make reasonable assumptions based on the schema and metadata.
        4. Optimize performance:
           - Use indexed columns in filtering conditions to speed up queries.
           - Aggregate results when large datasets are involved (e.g., using `SUM()`, `AVG()`, `GROUP BY`).
        
        5. Present answers effectively:
           - Use **Markdown** tables with proper column headers and alignments.
           - Provide **concise summaries** when large datasets are returned.

        6. For handling big result data:
           - The result is too large to display. Please refine your query or use filters to reduce the result size to show the `top ten` results only.

        """
        
        # Create the prompt and messages
        human_message = HumanMessagePromptTemplate.from_template("{input}").format(input=userinput)
        messages = [
            SystemMessage(content=prefix),
            human_message,
            AIMessage(content=suffix),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]

        prompt = ChatPromptTemplate.from_messages(messages)
        agent_executor = create_sql_agent(llm, db=db, agent_type="openai-tools", verbose=True, prompt=prompt,top_k=topk)

        # Execute the query
        response = agent_executor.invoke(f"Now answer this query: {userinput}")["output"]
        return {"response": response}
    except Exception as e:
        logging.error("Error handling query:", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while processing the request.")

# Basic endpoint for testing
@app.get("/")
def read_root():
    return {"message": "Welcome to my FastAPI app!"}

# Start the scheduler on app startup
@app.on_event("startup")
async def startup():
    scheduler.start()

# Shutdown the scheduler on app shutdown
@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
