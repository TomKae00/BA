import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import matplotlib.pyplot as plt
from weather_data import temperature_series
from data_Energieportal3 import all_close_potentials
import yaml
import seaborn as sns


with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

# Define inflation rates
inflation_2024 = 0.0353  # 3.53%
inflation_2025 = 0.022  # 2.2%
target_year = '2025'

def load_data(file_path):
    """Load data from CSV file and set the first column as index."""
    return pd.read_csv(file_path, index_col=0)


# Load the data
parameters_tci = load_data('data/table_3_data.csv')
# parameters_cop = load_data('data/table_4_data.csv')
costs_data = pd.read_csv('data/costs_function_2025.csv')
erzeugerpreisindex = load_data('data/Erzeugerpreisindex.csv')

erzeugerpreisindex = erzeugerpreisindex.set_index('Gewerbl Produkte')

# Apply inflation rates for 2024 and 2025
erzeugerpreisindex['2024'] = erzeugerpreisindex['2023'] * (1 + inflation_2024)
erzeugerpreisindex['2025'] = erzeugerpreisindex['2024'] * (1 + inflation_2025)


def adjust_specific_cost_updated(row):
    cost_data_year = str(row['Cost data (year EUR)'])
    gewerbl_produkte = row['Gewerbl Produkte']

    # Get the Erzeugerpreisindex for the given product for both the cost data year and the target year from the updated lookup
    index_cost_data_year = erzeugerpreisindex.loc[gewerbl_produkte, cost_data_year]
    index_target_year = erzeugerpreisindex.loc[gewerbl_produkte, target_year]

    # Calculate the inflation adjustment factor
    preissteigerungsfaktor_gewerbl_produkte = (index_target_year / index_cost_data_year) - 1

    # Adjust the specific cost
    adjusted_specific_cost = row['Specific cost (EUR/kW)'] * (1 + preissteigerungsfaktor_gewerbl_produkte)
    return adjusted_specific_cost


source_to_technology_name = {
    "Luft": "central air sourced heat pump",
    "Flussthermie": "central sourced-water heat pump",
    "Seethermie": "central sourced-sea heat pump",
    "Abwaerme": "central excess-heat heat pump"
}

all_close_potentials['Technology'] = all_close_potentials['Art'].map(source_to_technology_name)

# Now, determine available sources, including those always available
unique_sources_from_df = all_close_potentials['Technology'].unique()  # Now using the mapped names
available_sources = np.unique(np.append(unique_sources_from_df, [
    source_to_technology_name["Luft"],
    source_to_technology_name["Abwaerme"]
]))

temperature_series_outside = temperature_series  # Pandas series for outside temperature
temperature_series_flussthermie = temperature_series + 5  # Pandas series for Flussthermie temperature
temperature_series_seethermie = temperature_series + 7  # Pandas series for Seethermie temperature
abwaerme_temp = config['temperature_adjustments']['Abwaerme']

# Map of source types to their temperature series
temperature_series_map = {
    source_to_technology_name["Luft"]: temperature_series_outside,
    source_to_technology_name["Flussthermie"]: temperature_series_flussthermie,
    source_to_technology_name["Seethermie"]: temperature_series_seethermie,
    source_to_technology_name["Abwaerme"]: pd.Series(abwaerme_temp, index=temperature_series.index)

}

min_temp_thresholds = {
    source_to_technology_name[source]: threshold for source, threshold in config['min_temp_thresholds'].items()
}

T_supply_k = 60 + 273.15
T_return_k = 30 + 273.15


def calculate_for_source(hp_source):
    # Access the correct temperature series for the current source type
    T_source_c = temperature_series_map[hp_source]
    T_source_c_min = T_source_c.min()

    min_temp_threshold = min_temp_thresholds[hp_source]  # Get the threshold for the current source type
    if T_source_c_min < min_temp_threshold:
        T_source_c_min = min_temp_threshold

    dT_lift = T_supply_k - (T_source_c + 273.15)
    dT_lift_k_min = T_supply_k - (T_source_c_min + 273.15)


    return T_source_c, T_source_c_min, dT_lift, dT_lift_k_min


for hp_source in available_sources:
    T_source_c, T_source_c_min, dT_lift, dT_lift_k_min = calculate_for_source(hp_source)




def calculate_intermediate_max_supply_temp_R717(T_source_c_min):
    if T_source_c_min < 51.20:
        T_supply_fluid_max = 44.6 + 0.9928 * T_source_c_min
    else:
        T_supply_fluid_max = 104.20 - 0.1593 * T_source_c_min
    if T_source_c_min < 60.35:
        T_supply_TCI_min = 28.50 + 0.859 * T_source_c_min
    else:
        T_supply_TCI_min = 87.26 - 0.1102 * T_source_c_min
    T_supply_intermediate = (T_supply_fluid_max + T_supply_TCI_min) / 2
    return T_supply_intermediate


# Functions for Calculations
"""""
R717 needs to be calculated for T_source_c so for every component given. Needs to be adjusted at the moment not correct
"""""
def calculate_max_supply_temperatures(T_source_c_min):
    """Calculate and return the max supply temperatures for various fluids including R717."""
    temps = {'R290': 99.7, 'R134a': 101.7, 'R600a': 137.3, 'R600': 154.9, 'R245fa': 154.8, #'R1234yf': 95.3,
             'R717': calculate_intermediate_max_supply_temp_R717(T_source_c_min)}
    # Convert to Kelvin
    return {fluid: temp + 273.15 for fluid, temp in temps.items()}


max_supply_temps_k = calculate_max_supply_temperatures(T_source_c_min)


def check_constraints(fluid, T_supply_k):
    if fluid == 'R717':
        T_supply_intermediate = calculate_intermediate_max_supply_temp_R717(T_source_c_min)
        return T_supply_k <= max_supply_temps_k[fluid] and T_supply_k <= T_supply_intermediate
    else:
        return fluid in max_supply_temps_k and T_supply_k <= max_supply_temps_k[fluid]


def calculate_tci_strich_1000(D, E, F, T_supply_k, dT_lift_k_min):
    """Calculate TCI for a 1000 kW heat pump."""
    return D + E * dT_lift_k_min + F * T_supply_k


# Calculation Functions
def calculate_scaled_tci(TCI_strich_1000, alpha, X_kW_th):
    """Scale TCI based on heat pump size."""
    return TCI_strich_1000 * (X_kW_th / 1000) ** alpha


def calculate_fluid_1000(G, H, I, J, K, T_supply_k, dT_lift, T_source_c, min_temp_threshold):
    """Calculate fluid based on parameters, supply/dT_lift conditions, and a minimum temperature threshold."""
    fluid_series = pd.Series(index=T_source_c.index, dtype=float)

    # Vectorized condition: Set fluid to 0 if T_source_c is below the min_temp_threshold
    condition = T_source_c < min_temp_threshold
    fluid_series[condition] = 0

    # For temperatures above the threshold, calculate the fluid
    not_condition = ~condition
    fluid_series[not_condition] = (G + H * dT_lift[not_condition] + I * T_supply_k + J * dT_lift[not_condition]**2 +
                                 K * T_supply_k * dT_lift[not_condition])

    return fluid_series


def calculate_additional_costs(size_mw):
    """Calculate construction, electricity, and heat source investment costs in euros."""
    construction_cost = (0.084311 * size_mw + 0.021769) * 1e6  # Convert from Mio. EUR to EUR
    electricity_cost = (0.12908 * size_mw + 0.01085) * 1e6  # Convert from Mio. EUR to EUR
    if hp_source == 'central air sourced heat pump':
        heat_source_cost = (0.12738 * size_mw + 5.5007e-6) * 1e6
    elif hp_source == 'central excess-heat heat pump':
        heat_source_cost = (0.091068 * size_mw + 0.10846) * 1e6
    elif hp_source == 'central sourced-water heat pump':
        heat_source_cost = (0.12738 * size_mw + 5.5007e-6) * 1e6 #keine richtigen WERTE sondern die für gw
    elif hp_source == 'central sourced-sea heat pump':
        heat_source_cost = (0.12738 * size_mw + 5.5007e-6) * 1e6 #keine richtigen WERTE sondern die für gw
    else:
        raise ValueError("Invalid source type")
    return construction_cost, electricity_cost, heat_source_cost


cop_series = {}
results_list = []

# Main Calculation
sizes_kw_th = np.arange(100, 5001, 100)  # Heat pump sizes from 100 to 5000 kW_th # einfügen, dass null nicht genutzt werden kann

for hp_source in available_sources:
    min_temp_threshold = min_temp_thresholds[hp_source]
    # Calculate source-specific parameters
    (T_source_c, T_source_c_min, dT_lift, dT_lift_k_min) = calculate_for_source(hp_source)
    # Initialize storage for source-specific calculations
    cop_series[hp_source] = {}

    for fluid in parameters_tci.columns:
        if not check_constraints(fluid, T_supply_k):
            print(f"{hp_source}_{fluid} cannot be used due to constraints.")
            continue
        D, E, F, G, H, I, J, K, alpha = parameters_tci.loc[['D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'α'], fluid].values

        cop_series[hp_source][fluid] = []  # Initialize fluid list for the fluid
        cop_1000 = calculate_fluid_1000(G, H, I, J, K, T_supply_k, dT_lift, T_source_c, min_temp_threshold)
        cop_series[hp_source][fluid].append(cop_1000)
        mean_cop_1000 = cop_1000.mean()
        #Cost_data_year_EUR = '2023' # einfach mal hinzugefügt

        for size_kw_th in sizes_kw_th:
            size_mw = size_kw_th / 1000  # Convert kW_th to MW
            TCI_strich_1000 = calculate_tci_strich_1000(D, E, F, T_supply_k, dT_lift_k_min)
            scaled_tci = calculate_scaled_tci(TCI_strich_1000, alpha, size_kw_th)
            construction_cost, electricity_cost, heat_source_cost = calculate_additional_costs(size_mw)
            total_investment_cost = scaled_tci + construction_cost + electricity_cost + heat_source_cost
            specific_cost = total_investment_cost / size_kw_th if size_kw_th != 0 else 0
            #Cost_data_year_EUR = '2023' # einfach mal hinzugefügt

            # Append a dictionary for each size to the results list
            results_list.append({
                'Technology': hp_source,
                'fluid': fluid,
                'Size (kW)': size_kw_th,
                'Mean COP': mean_cop_1000,
                'TCI': scaled_tci,
                'Total Investment Costs': total_investment_cost,
                'Specific cost (EUR/kW)': specific_cost,
                'Cost data (year EUR)': '2023',
                'Gewerbl Produkte': "Waermepumpen,ausgen. Klimager. d. 2825 12, bis 15kW"
            })

    # Convert the list of dictionaries into a DataFrame
results_df = pd.DataFrame(results_list)

results_df.to_csv('data/results_analysis/heat_pump_analysis_results.csv', index=False)

"""""
pre-screening analysis of the different fluids for the used sources by using the stroed data about the costs 
get data in dataframes and then print them later in analysis 
"""""
# Calculate the average COP and mean specific costs directly from the DataFrame
average_cop_df = results_df.groupby(['Technology', 'fluid'])['Mean COP'].mean().reset_index(name='Average COP')
mean_specific_costs_df = results_df.groupby(['Technology', 'fluid'])['Specific cost (EUR/kW)'].mean().reset_index(name='Mean Specific Costs')

# Merge the two DataFrames on 'Heat Pump Source' and 'fluid'
efficiency_cost_df = pd.merge(average_cop_df, mean_specific_costs_df, on=['Technology', 'fluid'])

# Calculate the efficiency-cost ratio
efficiency_cost_df['Efficiency-Cost Ratio'] = efficiency_cost_df['Average COP'] / efficiency_cost_df['Mean Specific Costs']

specific_source = 'central air sourced heat pump'
source_df = efficiency_cost_df[efficiency_cost_df['Technology'] == specific_source]

# Identify fluids within a certain tolerance of the best ratio for each source
tolerance = 0.05  # 5% tolerance for example


# Function to filter fluids based on tolerance
def filter_best_fluids(group):
    best_ratio = group['Efficiency-Cost Ratio'].max()
    return group[group['Efficiency-Cost Ratio'] >= (1-tolerance) * best_ratio]


selected_fluids_df = efficiency_cost_df.groupby('Technology').apply(filter_best_fluids).reset_index(drop=True)

# selected fluids prepare for export!

filtered_results_df = pd.merge(results_df, selected_fluids_df,
                               on=['Technology', 'fluid'],
                               how='inner')

# Now, filtered_results_df contains only the data for selected fluids per heat pump source
print(filtered_results_df)

# If you wish to convert this list to a DataFrame for further analysis or export

costs_data['Size (kW)'] = costs_data['Size (MW)'] * 1000
costs_data.drop('Size (MW)', axis=1, inplace=True)
# Append the new data to the existing DataFrame
combined_data = pd.concat([costs_data, filtered_results_df], ignore_index=True)

combined_data['Adjusted Specific Cost (EUR/kW)'] = combined_data.apply(adjust_specific_cost_updated, axis=1)


# Fit a power-law model for each technology
if 'fluid' not in combined_data.columns:
    combined_data['fluid'] = 'NA'  # Assign a default value for entries without a fluid

combined_data['fluid'].fillna('NA', inplace=True)

# Now, adapt the loop to handle both cases
power_law_models = {}

power_law_models['central water tank storage'] = {'beta': 7450, 'alpha': -0.47}


def fit_power_law(group):
    log_size = np.log(group['Size (kW)'])
    log_specific_cost = np.log(group['Specific cost (EUR/kW)'])
    X_log = log_size.values.reshape(-1, 1)
    y_log = log_specific_cost.values
    model = LinearRegression().fit(X_log, y_log)
    return model.coef_[0], np.exp(model.intercept_)


# Applying the function in the loop
for (technology, fluid), group in combined_data.groupby(['Technology', 'fluid']):
    alpha, beta = fit_power_law(group)
    combined_key = f"{technology}_{fluid}" if fluid != 'NA' else technology
    power_law_models[combined_key] = {'alpha': alpha, 'beta': beta}

# Convert power law models to a DataFrame for easier analysis or export
power_law_models_df = pd.DataFrame.from_dict(power_law_models, orient='index')
power_law_models_df.to_csv('data/power_law_models.csv')

"""""

# Visualize the cost function for a specific technology, e.g., Electric Boiler
tech = 'central electric boiler'
alpha = power_law_models[tech]['alpha']
beta = power_law_models[tech]['beta']
electric_boiler_data = data[data['Technology'] == tech]
x_range = np.linspace(min(electric_boiler_data['Size (kW)']), max(electric_boiler_data['Size (kW)']), 100)
y_range = beta * x_range ** alpha
plt.figure(figsize=(10, 6))
plt.scatter(electric_boiler_data['Size (kW)'], electric_boiler_data['Specific cost (EUR/kW)'], color='blue', label='Data Points')
plt.plot(x_range, y_range, color='red', label='Cost Function (EUR/kW)')
plt.xlabel('Size (kW)')
plt.ylabel('Specific Cost (EUR/kW)')
plt.title(f'{tech} Cost Function with Data Points (EUR/kW)')
plt.legend()
plt.grid(True)
plt.show()

"""""
